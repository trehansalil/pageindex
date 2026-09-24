# Architecture Defect Zones Audit — 2026-08-17 POST-FIX

**Date:** 2026-08-17
**Sources:** 6 history miners, 3 code maps

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|---|---|---|---|
| 1 | Garble Detection Surface Fragmentation | critical | 14 | helpers.py, script.py, converters.py, client.py |
| 2 | Mutable ExtractionState Recovery Pipeline | critical | 11 | client.py, helpers.py |
| 3 | Split Verdict Authority (validate_tree / REASON_POLICY / classify_verdict) | critical | 9 | helpers.py |
| 4 | Picture Recovery / OCR Enrichment Conflation | high | 10 | converters.py, client.py, picture_plane.py, config.py |
| 5 | Verdict Persistence Dual-Path Inconsistency | high | 6 | storage.py, client.py, worker.py |
| 6 | Arabic/RTL Pipeline Bolt-On Architecture | high | 9 | converters.py, helpers.py, script.py, client.py |
| 7 | God Function Orchestration (pdf_to_markdown_docling) | medium | 6 | converters.py, helpers.py |
| 8 | Env-Var Flag Proliferation Without Interaction Registry | medium | 5 | config.py, converters.py, helpers.py, client.py |

## Zone Details

### Zone 1: Garble Detection Surface Fragmentation

**Severity:** critical | **Bug count:** 14

**Description:** Garble detection runs at 8+ distinct call sites (TREE_BULK, NODE, FLAT_MARKDOWN, PAGE_TEXT_LAYER, DOCUMENT_FALLBACK, REGION, RETRY_COMPARISON, IMAGE_ENRICHMENT) via `check_garble` with different `GarbleContext` values, each selecting different normalization strategies and short-circuit rules. The underlying `garble_prongs` function has 9 independent heuristic prongs (null_replacement_bytes, glyph_marker, control_chars, pua_chars, presentation_forms, single_letter_fragments, digit_ratio, token_repetition, latin_gibberish), several gated on `expected_script` — a parameter that historically was not threaded to callers. Script inference itself is duplicated: `helpers.py:1485` `_infer_script` (extended Latin U+00C0-U+024F, min 10 chars, min 5 script chars) vs `script.py:148` `infer_script` (ASCII-only alpha, no guards, no min-length). `converters.py:1660` imports script.py's `infer_script` for text-layer checks while `helpers.py:1541` uses its own `_infer_script` for node garble checks — the same text evaluated by two different script detectors can produce different `expected_script` values, silently enabling or disabling the `latin_gibberish` prong.

#### Mechanism
Every new garble fix adds a prong to `garble_prongs` or adjusts a context-specific short-circuit (like `GARBLE_SHORT_TEXT_DEFAULT` for FLAT_MARKDOWN < 200 chars), but these changes interact unpredictably across the 8 call sites. A prong designed for one context (e.g. `single_letter_fragments` for Arabic NODE checking) fires at another context with different text characteristics, producing false positives or false negatives. The `expected_script` parameter was historically unpropagated (RFC-019 D2, RFC-020 F2, RFC-025) meaning callers silently disabled the `latin_gibberish` prong for exactly the documents it was designed to catch. The short-text garble-by-default rule (`helpers.py:1398-1404`) bypasses ALL prong logic for FLAT_MARKDOWN < 200 chars with an original garbling defect, creating a binary on/off path that no prong refinement can influence. False positives from one context (e.g. `<!-- image -->` triggering `token_repetition` at `helpers.py:1316-1321`) cascade into wrong verdicts or unnecessary VLM fallbacks at another.

#### History
a. RFC-019 D2: `latin_gibberish` prong gated on `expected_script` never passed by two main callers.
b. RFC-020 F2: threading `expected_script` from filename into garble gate destroyed PictureItem segmentation via forced OCR.
c. RFC-021 QF1: reverting F2's forced-OCR to fix the PictureItem destruction.
d. RFC-023 D0: `_text_layer_has_content` garble-unaware, garbled text layers passed 20-char check.
e. RFC-023 D3: `<!-- image -->` markers triggered false-positive garble (token_repetition 100%).
f. RFC-025: four interconnected bugs — `_script_from_filename` returns None for German, `latin_gibberish` gated on non-None, `classify_verdict` lacks `expected_script`, hysteresis relaxes threshold.
g. RFC-013 D7/RFC-015: PUA-only garble detection missed RTL word-splitting, presentation-forms, Latin-in-Arabic mojibake.
h. RFC-033 D1: `garble_ratio` full-text tautology locked ratio to 1.0.
i. RFC-029 D4: `_repeating_token_density` returns 0.0 for <20 tokens, OCR keep-best never fires.

#### Code Evidence
`helpers.py:1251-1337` garble_prongs (9 independent prongs). `helpers.py:1374-1416` check_garble (8 GarbleContext values, short-text default at 1398-1404). `helpers.py:1485-1510` _infer_script (extended Latin + min guards). `script.py:148-159` infer_script (ASCII-only, no guards). `converters.py:1659-1662` late-importing check_garble+infer_script from two different modules. `helpers.py:1541` using _infer_script. `helpers.py:1316-1321` token_repetition with HTML comment strip. `helpers.py:1323-1335` latin_gibberish gated on expected_script != Latn.

#### Key Files
- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/script.py
- src/pageindex_mcp/converters.py
- src/pageindex_mcp/client.py

#### Simplification Proposal
**(1) Core Simplification**

Replace the 8-value `GarbleContext` enum and its behavioral dispatch inside `check_garble` with a `GarblePolicy` dataclass that each call site constructs explicitly — carrying `blob_kind`, `short_text_default_eligible` (bool), and `script_resolver` (a callable that returns `str | None`). Unify `_infer_script` (helpers.py:1485) and `infer_script` (script.py:148) into a single `infer_script` in script.py that accepts optional `extended_latin` and `min_length` parameters, eliminating the dual-detector divergence. The function `check_garble` then becomes a pure pipeline: resolve script, normalize blob, run prongs, OR sparse-mojibake — with zero context-specific branching.

**(2) Concrete Restructuring Steps**

Step A — Unify script inference (script.py, helpers.py):
- Extend `script.py:infer_script` to accept `extended_latin: bool = True` and `min_chars: int = 5, min_length: int = 10` keyword args, incorporating the guards from `_infer_script`.
- Delete `helpers.py:_infer_script` (~25 lines). All callers switch to `from .script import infer_script`.
- Update converters.py:1660 and helpers.py:1541 to use the unified function.
- Delta: -25 lines in helpers.py, +8 lines in script.py. Net: -17 lines.

Step B — Replace GarbleContext enum with GarblePolicy dataclass (helpers.py):
- Define `GarblePolicy` with fields: `blob_kind: BlobKind`, `short_text_garble_default: bool = False`, `original_defect: TreeDefect | None = None`.
- Delete the `GarbleContext` enum (~13 lines).
- Rewrite `check_garble` to accept `policy: GarblePolicy` instead of `context: GarbleContext + original_defect`. The function body drops all `if context ==` branches; it just reads policy fields. (~20 lines simplified to ~10 lines).
- Delta: +8 lines (dataclass), -13 lines (enum), -10 lines (branch removal). Net: -15 lines.

Step C — Push policy construction to call sites (client.py, converters.py, helpers.py):
- Each of the ~15 `check_garble(...)` calls constructs a `GarblePolicy(blob_kind=BlobKind.RAW_MARKDOWN, short_text_garble_default=True)` or similar at the call site, making the behavior explicit and locally readable.
- converters.py:1659-1665 stops late-importing `infer_script` from script.py separately; it uses the unified one already resolved at the call site or passed as `expected_script`.
- Delta: ~+1 line per call site (15 sites), net +15 lines. But each site is now self-documenting.

Step D — Delete `_script_from_filename` indirection (helpers.py:1513-1523):
- Inline the 2-line body (`detect_ocr_langs(filename)` + `"ara" in langs`) at the 1-2 call sites that use it. Remove the late import.
- Delta: -11 lines.

Total estimated delta: approximately -28 net lines, but the real value is structural: zero hidden context-dependent branching, zero dual-detector divergence, zero late-import script resolution from mismatched modules.

**(3) Historical Bug Classes Prevented**

- **RFC-019 D2 / RFC-025 (latin_gibberish silently disabled):** Would not have happened. Script resolution is explicit at each call site via the unified `infer_script`; there is no hidden "expected_script defaults to None, which disables the prong" path. The policy makes the caller explicitly decide.
- **RFC-020 F2 / RFC-021 QF1 (expected_script threading destroyed PictureItem):** Would not have happened. Each call site constructs its own policy with its own script decision; there is no single `expected_script` parameter threaded through unrelated code paths.
- **RFC-025 (_script_from_filename returns None for German):** Would not have happened. The dual-detector divergence (helpers `_infer_script` vs script.py `infer_script`) that produced different results for the same text is eliminated.
- **RFC-023 D3 (HTML comment false-positive garble):** Still requires the `re.sub(r"<!--.*?-->", "")` strip in the `token_repetition` prong, but the policy-based design makes it visible that `FLAT_MARKDOWN` callers should use `BlobKind.RAW_MARKDOWN` which strips comments during normalization, rather than relying on a hidden context branch.
- **RFC-033 D1 (garble_ratio tautology):** Not directly addressed by this restructuring (that bug is in `_garble_ratio`, not in dispatch), but the single-entry-point design makes it easier to audit because there is one place to verify ratio computation, not 8 context-dependent paths.
- **RFC-029 D4 (repeating_token_density returns 0.0 for <20 tokens):** Not directly addressed (prong-level bug), but the unified policy makes it clear that short-text blobs are treated differently, so the interaction between `short_text_garble_default` and the 20-token floor in `token_repetition` is locally visible instead of split across two locations.

**(4) Migration Risk and Sequencing**

Risk: Medium-low. The restructuring is mechanical (enum-to-dataclass, dual-function-to-single), but there are 15 call sites across 3 files, and each must be verified to produce identical behavior.

Incremental sequence:
1. **Step A first (script unification):** Lowest risk, zero behavioral change, fully testable in isolation. The two functions already produce identical results for Arabic text; the difference is only in extended-Latin handling and min-length guards. Add parameters to the canonical one, switch callers, delete the duplicate. Run full test suite.
2. **Step B+C together (policy dataclass):** Introduce `GarblePolicy` alongside `GarbleContext` first — make `check_garble` accept either (union type) during transition. Migrate call sites one file at a time (helpers.py first, then client.py, then converters.py). Each migration is independently testable. Once all callers use `GarblePolicy`, delete `GarbleContext` and the old signature.
3. **Step D last (inline `_script_from_filename`):** Trivial cleanup after Steps A-C land.

Key validation: The existing test suite plus the 25-doc corpus scoring pipeline are the safety net. Run `uv run pytest` after each step. Run `make ingest` on the full corpus after Step B+C to verify no verdict regressions.

**(5) Estimated Effort**

- Step A (script unification): 1-2 hours including tests.
- Step B+C (policy dataclass + call site migration): 3-4 hours including mechanical migration and test verification.
- Step D (inline cleanup): 30 minutes.
- Corpus regression check: 1-2 hours (automated, but wall-clock time for ingestion).
- Total: approximately 1 day of focused work.

---

### Zone 2: Mutable ExtractionState Recovery Pipeline

**Severity:** critical | **Bug count:** 11

**Description:** A single mutable `ExtractionState` dataclass (helpers.py:177-201, ~20 fields) is threaded through client.py's `index()` method: first through `_convert_to_tree`, then through 7 serial recovery methods (`_recover_ocr_escalation`, `_recover_rtl_repair`, `_recover_rtl_flat_compare`, `_recover_vlm_fallback`, `_recover_image_dominant_ocr`, `_recover_flat_prefer`, `_recover_landscape_reroute`), then `_finalize_routing`, then an exhaustive match/case dispatch. Each recovery method reads, mutates, and conditionally snapshots/restores state via `ExtractionSnapshot`. The `snapshot.restore()` method returns `gate_result` twice (once for `gate_result`, once for `original_gate_result` at helpers.py:169), creating implicit coupling. `_finalize_routing` (client.py:954-979) attempts post-hoc reconciliation but only runs when `state.route_overridden` is False and `state.ok` is False. The `flat_garble_unrecovered` field is set inside `_persist_flat_result` but acts as a pre-match orthogonal reject trigger at client.py:2176, creating non-local control flow where a persist method's side effect controls the caller's dispatch.

#### Mechanism
Each recovery method reads state fields set by prior methods (or by `_convert_to_tree`) and mutates them for subsequent methods. This serial-mutation chain means: (1) reordering any recovery method changes outcomes because each depends on what prior methods wrote into state; (2) adding a new `validate_tree` failure reason in helpers.py requires adding a corresponding handler in client.py's recovery methods — if omitted, the reason falls through to raise `LowQualityTreeError` (RFC-029 added 4 new reasons, none handled, causing 3 PASS->ERROR and 1 FAIL->ERROR regressions); (3) snapshot/restore can discard a second `validate_tree`'s `gate_result`, reverting the pipeline to stale defect information; (4) the `_finalize_routing` reconciliation only fires under specific conditions, so state can remain inconsistent (e.g. `ok=True` but `route=FLAT` from a pre-recovery assignment, which `_finalize_routing` skips).

#### History
a. RFC-023 D7: VLM crash left reason='garbling' but garbling was not in flat-routing reason check, skipping flat routing entirely.
b. RFC-023 D11: Fix-3 OCR escalation only fired on reason=='garbling' literal string, missing structural reasons (node_count<3, depth<2).
c. RFC-029 D0/D1/D2/D8: four new validate_tree failure reasons not handled in client.py recovery paths, all fall through to raise LowQualityTreeError (3 PASS->ERROR, 1 FAIL->ERROR).
d. RFC-022 B2-A: content_class assignment at client.py:707 silently overwritten by route_and_extract_flat at line 1004.
e. RFC-023 D5: synthetic-structure only fired when flat_structure was completely empty; rejected non-empty structure prevented it from firing.
f. RFC-017 P0a/RFC-020 F0: per-picture OCR splice removed from tree path, tree-path content recovery lost.

#### Code Evidence
`helpers.py:177-201` ExtractionState (20 mutable fields). `helpers.py:107-173` ExtractionSnapshot (restore() returns gate_result twice at line 169). `client.py:2148-2166` seven serial recovery calls. `client.py:954-979` _finalize_routing (conditional reconciliation). `client.py:2176` flat_garble_unrecovered pre-match guard. `client.py:1297-1319` _recover_ocr_escalation trigger (first_defect in GARBLING/NODE_GARBLING). `client.py:2188-2253` exhaustive match/case dispatch.

#### Key Files
- src/pageindex_mcp/client.py
- src/pageindex_mcp/helpers.py

#### Simplification Proposal
**(1) Core simplification (2-3 sentences)**

Replace the serial-mutation recovery pipeline with a **recovery-attempt list** pattern: each recovery method becomes a pure function that takes an immutable extraction snapshot plus file context and returns either `None` (no action) or a `RecoveryResult` dataclass containing the new tree, gate result, route override, and md_content. The `index()` caller iterates the list, applies the first (or best) successful result to the state exactly once, then runs a single `decide_route` call on the final defect — eliminating the need for `_finalize_routing`, `route_overridden`, `ExtractionSnapshot.restore()`, and the `flat_garble_unrecovered` side-channel entirely.

**(2) Concrete restructuring steps**

| Step | File | What | Lines delta |
|------|------|------|-------------|
| A. Define `RecoveryResult` frozen dataclass | helpers.py | Fields: `result`, `ok`, `reason`, `gate_result`, `md_content`, `pic_results`, `used_converter`, `total_chars`, `route_override: Route \| None`. Replaces `ExtractionSnapshot` + its `restore()` tuple. | +15, -68 (remove ExtractionSnapshot class + from_state + restore) |
| B. Convert each `_recover_*` method to return `RecoveryResult \| None` | client.py | Each method receives a frozen snapshot (the current `RecoveryResult` or initial extraction output) plus file-context args. Internally it can still call `_reconvert_and_revalidate` but on local variables, not on `state`. The snapshot/restore dance inside `_recover_ocr_escalation` (lines 1321-1454) becomes a simple "call, compare, return winner or None". | ~0 net (internal structure stays; removes ~30 lines of state mutation/restore, adds ~30 lines of local-var construction) |
| C. Build recovery loop in `index()` | client.py:2148-2166 | Replace 7 serial `await self._recover_*(state, ...)` calls with a list of recovery functions and a `for recover_fn in recoveries:` loop. After loop, single `decide_route(current.defect)` call replaces `_finalize_routing`. | -30 (remove _finalize_routing, route_overridden checks, pre-match garble guard), +15 (loop + final routing) |
| D. Inline garble check into dispatch | client.py:2176-2182 + 1781-1835 | Move the flat-path garble check out of `_persist_flat_result` into the `(False, Route.FLAT)` case arm, so `flat_garble_unrecovered` ceases to exist as a field. The garble check returns a boolean that the case arm acts on directly. | -20 (remove field, pre-match guard, duplicate checks), +5 (inline check) |
| E. Remove `route_overridden` field | helpers.py + client.py | Recovery methods that force a route (RTL flat compare, VLM fallback, flat prefer, landscape reroute) return their route in `RecoveryResult.route_override`. The loop in `index()` applies it. No separate boolean flag needed. | -15 scattered |
| F. Exhaustiveness compile-time guard | helpers.py:228-229 | Keep the existing `assert set(REASON_POLICY) == set(TreeDefect)` but add a parallel `assert set(TreeDefect) <= set(REASON_POLICY)` test (already present) AND a pytest parametrize test that every `TreeDefect` value is handled by at least one recovery method's guard condition or explicitly mapped to RAISE/PERSIST_FAIL in the policy table — this is what RFC-029 broke. | +25 (new test file or test function) |

**Overall delta**: approximately -90 lines production code, +25 lines test code.

**Key deletions**: `ExtractionSnapshot` class and `restore()` method (~68 lines), `_finalize_routing` method (~25 lines), `route_overridden` field and all checks (~15 lines), `flat_garble_unrecovered` field and pre-match guard (~20 lines).

**(3) Historical bug classes this would have prevented**

- **RFC-029 D0/D1/D2/D8 (unhandled new TreeDefect reasons)**: The exhaustiveness test (step F) would fail at CI time when a new TreeDefect is added without a corresponding policy entry or recovery handler. The current architecture only asserts policy-table exhaustiveness but not recovery-path coverage.
- **RFC-023 D7 (garbling reason not in flat-routing check)**: With a single `decide_route` call after all recovery, there is no "flat-routing reason check" to miss — routing is derived from the final defect, not matched against a string set inside a recovery method.
- **RFC-023 D11 (OCR escalation missing structural reasons)**: Recovery eligibility is driven by the policy table mapping, not ad-hoc `first_defect in (...)` checks that can diverge from the table.
- **RFC-022 B2-A (content_class silently overwritten)**: Removing mutation-during-persist eliminates the class of bugs where a persist method changes state that the caller later reads.
- **RFC-023 D5 (synthetic-structure non-firing)**: Recovery methods returning `RecoveryResult` instead of mutating state means each method sees a clean input, not residue from a prior failed recovery.
- **Snapshot restore returning gate_result twice (helpers.py:169)**: Eliminated entirely — no snapshot/restore pattern needed.

**(4) Migration risk and sequencing**

Risk: The recovery methods contain substantial domain logic (OCR lang detection, garble density comparison, RTL reversal checks). Refactoring them simultaneously is high-risk.

Incremental sequence:
1. **Phase A (low risk, 1 PR)**: Add the exhaustiveness test (step F) and the `RecoveryResult` dataclass. Keep existing code running. This is purely additive and immediately catches future RFC-029-class regressions.
2. **Phase B (medium risk, 1 PR per recovery method)**: Convert one recovery method at a time to return `RecoveryResult | None`, starting with the simplest (`_recover_landscape_reroute`, ~15 lines of logic). The `index()` loop temporarily handles both old-style (mutates state) and new-style (returns result) methods via a thin adapter.
3. **Phase C (medium risk, 1 PR)**: Once all 7 methods return `RecoveryResult`, remove `_finalize_routing`, `route_overridden`, and `ExtractionSnapshot`. Convert the loop to apply the single winner.
4. **Phase D (low risk, 1 PR)**: Inline flat garble check, delete `flat_garble_unrecovered` field.

Each phase is independently shippable and testable against the existing corpus. The adapter in Phase B means no big-bang switchover.

Regression guard: run the full 25-doc corpus ingest-score cycle after each phase and diff verdicts against the pre-change baseline. Any verdict change is a blocker.

**(5) Estimated effort**

- Phase A: 0.5 day (dataclass + test)
- Phase B: 2-3 days (7 methods, one at a time, corpus verify after each)
- Phase C: 1 day (loop refactor + delete dead code)
- Phase D: 0.5 day (inline garble check)

Total: 4-5 days of focused work, spread across 9-10 PRs for safe incremental delivery.

---

### Zone 3: Split Verdict Authority (validate_tree / REASON_POLICY / classify_verdict)

**Severity:** critical | **Bug count:** 9

**Description:** The verdict decision is split across three independent data structures with manual synchronization. GATE_TABLE (helpers.py:1779-1790) defines 10 gates and their TreeDefect enum values. REASON_POLICY (helpers.py:213-226) maps each TreeDefect to a recovery policy (RETRY_OCR, RAISE, PERSIST_FAIL, CAP_MARGINAL). HARD_FAIL_DEFECTS (helpers.py:236-242) defines which defects cause immediate FAIL in classify_verdict. These are manually "kept in sync" per the comment at line 234, but they disagree by design: GARBLING maps to RETRY_OCR in REASON_POLICY (allowing retry) but is in HARD_FAIL_DEFECTS (causing FAIL if unresolved). SUSPECT_DENSITY maps to PERSIST_FAIL in REASON_POLICY but is also in HARD_FAIL_DEFECTS. classify_verdict (helpers.py:2052-2274, 222 lines) is a monolithic grouped-rule engine with 4+ dispatch branches, 5+ promotion paths, two caps via a _pass() closure, and a MARGINAL fallback. It re-checks signals already evaluated by validate_tree (e.g. sig.node_count >= 3 at line 2212 duplicates gate 2, sig.depth >= 2 duplicates gate 3), and for flat docs (validate_result=None) constructs synthetic TreeSignals bypassing all 10 gates.

#### Mechanism
When a new gate is added to GATE_TABLE, the developer must also update REASON_POLICY, potentially HARD_FAIL_DEFECTS, and add handling in classify_verdict's promotion/cap logic. These four sites have no programmatic derivation linking them — the comment at line 234 says "kept in sync" but the sync is manual. classify_verdict's nested _pass() closure (line 2174) applies caps to every PASS-returning branch, but each branch has different preconditions (image-enrichment rescue bypasses max_leaf_ratio, cat_b checks placeholder_ratio, small-doc exemption has its own bounds). Adding a new promotion path or adjusting a cap requires understanding all other paths' interactions. The flat-doc path (validate_result=None) creates entirely separate TreeSignals and lifts only is_reordered into defects, so flat docs skip per-node garble checks, RTL reversal detection, empty-node contamination, and density floors. Gate ordering in GATE_TABLE determines primary defect priority, while classify_verdict's _masked_hard_fails check (line 2153-2156) re-ranks defects by _GATE_PRIORITY — a second ordering that can disagree with the primary if co-firing defects include both hard-fail and non-hard-fail entries.

#### History
a. RFC-022 B2-B: max_leaf_ratio hard-FAIL fired before QF2a image-enrichment promotion could rescue doc 13 pie chart.
b. RFC-023 D4: cat_b_promoted had no min text-length check, doc 21 with 210 chars of bare <!-- image --> promoted to PASS.
c. RFC-024 D0/RFC-023 D10/RFC-025 D0: PASS_MAX_LEAF_RATIO widened 0.17->0.20->0.30->0.30-with-hysteresis chasing non-deterministic Docling heading selection.
d. RFC-025: hysteresis relaxation let 61%-garbled Haftpflicht flip FAIL->PASS.
e. RFC-029 D1: low_content_density gate with 500 chars/node threshold too aggressively rejected well-structured legal docs.
f. RFC-014: image_enrichment_promoted bypassed content-volume gates, docs with 38 chars/2 blocks passed PASS.

#### Code Evidence
`helpers.py:1779-1790` GATE_TABLE (10 gates). `helpers.py:213-226` REASON_POLICY. `helpers.py:236-242` HARD_FAIL_DEFECTS (manual sync comment at line 234). `helpers.py:2052-2274` classify_verdict (222 lines, 5+ promotion paths). `helpers.py:2174-2182` _pass() closure with bidi_degraded + depth_adequacy caps. `helpers.py:2188-2207` image-enrichment rescue BEFORE max_leaf_ratio hard-fail (intentional bypass). `helpers.py:2212-2217` base PASS re-checking node_count >= 3 and depth >= 2 (duplicates gates 2/3). `helpers.py:2136-2138` flat-doc path lifts only is_reordered, skipping 9 other gates.

#### Key Files
- src/pageindex_mcp/helpers.py

#### Simplification Proposal
**(1) Core simplification (2-3 sentences)**

Consolidate the per-defect metadata (recovery policy, hard-fail flag, verdict-cap behavior) into a single `GateSpec` dataclass attached directly to each `TreeDefect` enum member or to each GATE_TABLE entry, eliminating REASON_POLICY, HARD_FAIL_DEFECTS, and _GATE_PRIORITY as separate manually-synchronized structures. Then replace classify_verdict's 222-line monolithic rule engine with a two-phase pipeline: Phase 1 collects the verdict floor/ceiling from the GateSpec of every fired defect (hard-fail = floor FAIL; cap-marginal = ceiling MARGINAL); Phase 2 runs ordered promotion rules (image-enrichment, base-PASS, cat_a/b/c, small-doc) that can only produce verdicts within the [floor, ceiling] band. This makes it structurally impossible for a new gate to exist without its policy/hard-fail/priority metadata — they are the same object.

**(2) Concrete restructuring steps**

Step A — Define `GateSpec` dataclass (~15 new lines in helpers.py near line 68):
```python
@dataclass(frozen=True)
class GateSpec:
    defect: TreeDefect
    gate_fn: _GateFn
    policy: _ReasonPolicy
    hard_fail: bool   # replaces HARD_FAIL_DEFECTS membership
    # priority is implicit: index in GATE_TABLE
```

Step B — Merge GATE_TABLE + REASON_POLICY + HARD_FAIL_DEFECTS into a single `GATES: list[GateSpec]` (~20 lines replacing ~30 lines at lines 213-242 + 1779-1797). Derive the old names as views for any remaining consumers:
```python
REASON_POLICY = {g.defect: g.policy for g in GATES}
HARD_FAIL_DEFECTS = frozenset(g.defect for g in GATES if g.hard_fail)
_GATE_PRIORITY = {g.defect: i for i, g in enumerate(GATES)}
```
These derived dicts stay temporarily for backward compat but are no longer manually maintained. The assert at line 228 becomes a GateSpec-vs-TreeDefect completeness check on the unified list. Net delta: ~-10 lines.

Step C — Refactor classify_verdict into a pipeline (~-60 lines net):
- Extract `_compute_verdict_band(all_defects, GATES) -> (floor_verdict, ceiling_verdict)` (~20 lines). Floor = "FAIL" if any fired defect has `hard_fail=True`, else "MARGINAL". Ceiling = "MARGINAL" if any fired defect has `policy == CAP_MARGINAL`, else "PASS". This replaces GROUP 1 (lines 2140-2156, ~17 lines) and the _pass() closure (lines 2174-2182, ~9 lines).
- Keep GROUP 2 promotion rules as-is but each `return _pass(reason)` becomes `return _clamp(verdict_band, "PASS", reason)` and each `return "FAIL", ...` becomes `return _clamp(verdict_band, "FAIL", reason)`. The _clamp function (~5 lines) enforces floor/ceiling. This eliminates the need for _pass() and the separate _bidi_degraded variable.
- The flat-doc path (lines 2136-2138) stays: when validate_result is None, sig.is_reordered is lifted into _all_defects, which then flows through _compute_verdict_band naturally — no special case needed in classify_verdict itself.
- Remove the duplicated `sig.node_count >= 3` / `sig.depth >= 2` checks from the base-PASS branch (lines 2212-2213). These are already gates 2 and 3 in GATE_TABLE; if they fire, _compute_verdict_band sets floor to FAIL (via HARD_FAIL_DEFECTS containing REORDERED but not NODE_COUNT_LOW/DEPTH_LOW). Actually NODE_COUNT_LOW and DEPTH_LOW map to RAISE, not hard-fail. So the base-PASS branch's checks are genuinely redundant with the gate evaluation — if gate 2 or 3 fired, the defect is in all_defects, and since they map to RAISE/FLAT routing, the document would have been routed flat and classify_verdict would receive validate_result=None. For the tree path (validate_result present and OK), these gates did not fire, so node_count >= 3 and depth >= 2 are guaranteed. The checks can be deleted. ~-4 lines.

Step D — validate_tree consumes GATES instead of GATE_TABLE (~0 line delta, just a reference rename).

File targets: `src/pageindex_mcp/helpers.py` only.
Rough line-count delta: ~-50 to -70 lines net (consolidation of three data structures into one, plus classify_verdict shrinks from 222 to ~150 lines).

**(3) Historical bug classes prevented**

- RFC-022 B2 (max_leaf_ratio hard-fail before image-enrichment rescue): With the band model, image-enrichment would set ceiling to PASS normally, and max_leaf_ratio would need to be a hard-fail GateSpec to override — the ordering dependency disappears because hard-fail is a property of the defect, not of code position.
- RFC-023 D4 (cat_b_promoted without min text-length): The _clamp function would have enforced the band ceiling, but more importantly the unified GateSpec makes it obvious that promotion rules cannot bypass hard constraints — the band is computed before promotions run.
- RFC-025 (hysteresis relaxation letting garbled doc flip FAIL->PASS): GARBLING has hard_fail=True in GateSpec. If garbling fires in any gate, the band floor is FAIL — no promotion path can override it, regardless of threshold tuning. The current code achieves this through HARD_FAIL_DEFECTS but the manual sync means a threshold change in a gate function can decouple from the hard-fail set.
- RFC-029 D1 (low_content_density too aggressive): With GateSpec, the hard_fail flag for LOW_CONTENT_DENSITY is visible right next to the gate function and threshold, making it obvious that changing the threshold changes what gets hard-failed. No cross-file mental model needed.
- All "forgot to update REASON_POLICY when adding gate" bugs: structurally impossible because GateSpec requires policy at construction time, and the assert checks GATES covers all TreeDefect members.

**(4) Migration risk and sequencing**

Risk is moderate — classify_verdict is the most-patched function in the codebase and has extensive test coverage (the corpus audit runs validate its output).

Incremental sequence:
1. **Wave 1 (low risk)**: Introduce GateSpec dataclass. Build GATES list. Derive REASON_POLICY, HARD_FAIL_DEFECTS, _GATE_PRIORITY as computed views from GATES. GATE_TABLE becomes `[(g.gate_fn, g.defect) for g in GATES]`. Zero behavioral change — existing code reads the same dicts. Run full test suite + corpus spot-check.
2. **Wave 2 (moderate risk)**: Extract `_compute_verdict_band()` and `_clamp()`. Refactor GROUP 1 and _pass() closure to use them. classify_verdict body shrinks but output is identical for all inputs. Run full corpus re-score and diff against prior run (corpus-diff skill).
3. **Wave 3 (low risk)**: Remove redundant `node_count >= 3` / `depth >= 2` checks from base-PASS branch. Remove the derived dict definitions once all consumers read GateSpec directly. Delete GATE_TABLE alias.
4. **Wave 4 (optional)**: Move gate functions from standalone `_gate_*` functions into GateSpec methods or keep as-is (they are already well-factored).

Each wave is independently committable and revertible. The corpus-diff between waves is the regression gate.

Main risk: the flat-doc path (validate_result=None) constructs synthetic signals and bypasses gates entirely. The band model handles this correctly (empty all_defects = floor MARGINAL, ceiling PASS) but the flat-doc promotion rules (cat_b, small_doc) must still be tested against flat docs that previously got PASS. A corpus spot-check on flat docs is required after Wave 2.

**(5) Estimated effort**

- Wave 1: 1-2 hours (mechanical restructuring, no logic change)
- Wave 2: 2-3 hours (classify_verdict refactor + corpus diff validation)
- Wave 3: 30 minutes (cleanup)
- Total: 4-6 hours of focused work, including corpus validation runs between waves

---

### Zone 4: Picture Recovery / OCR Enrichment Conflation

**Severity:** high | **Bug count:** 10

**Description:** The picture recovery pipeline conflates three logically distinct operations into coupled code paths: (1) per-picture OCR enrichment (_recover_picture_text at converters.py:2072-2381, 310 lines, complexity ~40), (2) page-level OCR escalation for garbled/scanned PDFs (client.py _recover_ocr_escalation), and (3) image-enrichment-based verdict promotion (classify_verdict's image_enrichment_rescue path). These operations share config flags (OCR_ESCALATION controls both per-picture and garble-triggered escalation by default via config.py coupling), containment checks (body_for_containment snapshot in _run_fallback_pipeline at converters.py:3192), and coverage gates (_text_layer_has_content at converters.py:1646-1664, gated on _TEXT_LAYER_GARBLE_CHECK_ENABLED, feeding into _COVERAGE_EXEMPT_NO_TEXT_LAYER, bounded by _MAX_FULLPAGE_PICTURE_OCR_REGIONS). The coverage gate's skip decision (skip OCR for regions > 60% of page area) cannot communicate "deliberately skipped" vs "genuinely failed" to splice_figure_markers downstream, leaving unresolved <!-- image --> markers in output.

#### Mechanism
A fix to one of the three operations inadvertently breaks the others because they share gating logic and data structures. Tightening the coverage filter (RFC-018 D0) suppressed content recovery on scanned PDFs that have no text layer — the filter cannot distinguish "large chart that should be OCR-ed" from "full scanned page that is the only content source". The body_for_containment snapshot (converters.py:3192) fixes one ordering bug (RFC-024 D1 where _document_level_text_fallback made all clip_text look contained) but creates a fragile stage-ordering constraint — moving any stage between the snapshot and _recover_picture_results breaks containment. PictureResult list construction via Python list multiplication created shared references (RFC-019 D0, popping png_bytes on one entry mutated all N). The splice_figure_markers count-mismatch guard (converters.py:1630-1636) bails out entirely on mismatch rather than degrading gracefully, leaving all markers unresolved.

#### History
a. RFC-017 P0a/RFC-020 F0: per-picture OCR splice removed from tree path, 5 Arabic scanned PDFs collapsed with 60% content loss.
b. RFC-018 D0/RFC-017 P0b: page-coverage filters unconditionally skipped full-page picture regions even when no text layer exists.
c. RFC-020 F1: coverage exemption for no-text-layer pages.
d. RFC-023 D0: _text_layer_has_content garble-unaware, false-positive blocked coverage exemption.
e. RFC-023 D1: splice_figure_markers count-mismatch guard bailed out, leaving all markers unresolved.
f. RFC-019 D0/RFC-020 F4: PictureResult list multiplication created shared dict references.
g. RFC-024 D1: _document_level_text_fallback suppressed picture recovery via false-positive containment.
h. RFC-035 D2: landscape rasterize-rotate-reextract caused timeout and chart fragmentation.

#### Code Evidence
`converters.py:2072-2381` _recover_picture_text (310 lines, coverage gate at 2153-2232, clip_text containment at 2238-2280). `converters.py:2500-2570` _recover_picture_results (ocr_mode dispatch). `converters.py:3162-3211` _run_fallback_pipeline (body_for_containment snapshot at line 3192). `converters.py:1646-1664` _text_layer_has_content (late-imports check_garble at 1659). `config.py:41-59` OCR_ESCALATION->OCR_ESCALATION_GARBLE/OCR_ESCALATION_PER_PICTURE coupling. `picture_plane.py:133-155` decide_ocr_mode.

#### Key Files
- src/pageindex_mcp/converters.py
- src/pageindex_mcp/client.py
- src/pageindex_mcp/picture_plane.py
- src/pageindex_mcp/config.py

#### Simplification Proposal
**(1) Core Simplification (2-3 sentences)**

Replace the interleaved gate/crop/skip logic inside `_recover_picture_text` (converters.py:2072-2381, 310 lines) with a two-phase architecture: Phase A is a pure-function per-region classifier that returns a typed `RegionDisposition` enum (`OCR`, `CLIP_CAPTURE`, `RETAIN_SKIP`, `DROP`) plus a `SkipReason`, using page/region metadata as input but performing no I/O. Phase B is a dumb executor that crops and OCRs only regions Phase A tagged `OCR`, crops-only for `RETAIN_SKIP`, and captures clip_text for `CLIP_CAPTURE`. This eliminates the current pattern where gating decisions, pixmap cropping (duplicated 4 times), skip-reason assignment, and retained-skip handling are all interleaved in one 310-line loop with 8 `continue` paths and 3 near-identical `page.get_pixmap(clip=rect, dpi=300)` / `page.set_rotation` blocks.

**(2) Concrete Restructuring Steps**

Step 1: Extract `_crop_page_region(page, rect) -> bytes` helper in converters.py to deduplicate the 4 identical rotation-reset + get_pixmap + tobytes blocks (lines 2186-2196, 2214-2226, 2259-2267, 2292-2296). Net: ~-35 lines.

Step 2: Extract `_classify_region(region, page, md_norm, expected_script, fullpage_count, config_flags) -> tuple[RegionDisposition, SkipReason | None]` as a pure function in `picture_plane.py`. Moves all the gate logic (coverage threshold + text-layer check + garble check + clip-text containment + decorative-icon filter) out of the imperative loop into a testable decision function. Move the 6 config constants it depends on (`_PICTURE_PAGE_COVERAGE_THRESHOLD`, `_COVERAGE_EXEMPT_NO_TEXT_LAYER`, `_MAX_FULLPAGE_PICTURE_OCR_REGIONS`, `_CLIP_TEXT_CAPTURE_ENABLED`, `_TEXT_LAYER_GARBLE_CHECK_ENABLED`, `_DECORATIVE_ICON_MIN_DIM_PT`, `_REGION_AWARE_TEXT_CHECK_ENABLED`) into a frozen dataclass `PictureGateConfig` in picture_plane.py, constructed once from env vars. Net: picture_plane.py +80 lines, converters.py -120 lines. Total delta: ~-40 lines.

Step 3: Rewrite the `_recover_picture_text` main loop as: classify all regions -> crop all non-DROP regions -> OCR the OCR-tagged subset. The loop body shrinks to ~15 lines (classify, crop if needed, append to correct bucket). The Phase-2 parallel OCR block (lines 2351-2360) stays unchanged. Net: converters.py -80 lines.

Step 4: Decouple `body_for_containment` from stage ordering in `_run_fallback_pipeline` (converters.py:3162-3211). Instead of a fragile snapshot-before-text-fallback, pass the containment text as an explicit parameter to `_recover_picture_results` from the caller (`pdf_to_markdown_docling`), computed as `md` right after `normalize_indented_headings` returns. Delete `_run_fallback_pipeline` entirely; inline its two stage calls at the call site (it is called once). Net: -20 lines, eliminates the stage-ordering constraint.

Step 5: Make `RegionDisposition` carry `DELIBERATELY_SKIPPED` vs `FAILED` distinction. `bind_markers` and `splice_figure_markers` can then use this to decide per-marker behavior (inject chart text, strip, or leave neutral) without the current landscape-fallback string-matching filter. Net: picture_plane.py +10 lines, converters.py splice functions -10 lines.

File targets and deltas:
- `src/pageindex_mcp/picture_plane.py`: +90 lines (RegionDisposition enum, PictureGateConfig dataclass, _classify_region function)
- `src/pageindex_mcp/converters.py`: -200 lines (deduplicated crops, extracted classification, inlined _run_fallback_pipeline, simplified splice functions)
- `src/pageindex_mcp/config.py`: -10 lines (move 7 picture-gate constants to PictureGateConfig in picture_plane.py; config.py retains only the 3 OCR_ESCALATION toggles)
- `src/pageindex_mcp/client.py`: ~0 lines (no structural change; passes containment text explicitly instead of relying on _run_fallback_pipeline ordering)
- Estimated net delta: **-120 lines**

**(3) Historical Bug Classes Prevented**

- **RFC-018 D0 / RFC-020 F1 (coverage filter suppressing scanned PDFs)**: The `_classify_region` pure function would make the coverage-vs-text-layer decision unit-testable in isolation. The current bug (filter cannot distinguish "large chart" from "full scanned page") happened because the gate logic was buried inside an imperative loop and never tested with scanned-page inputs. With the extracted classifier, a test like `assert classify(region_covering_95pct, page_with_no_text_layer) == OCR` would have caught it before merge.
- **RFC-024 D1 (body_for_containment suppression)**: Eliminated entirely. The fragile snapshot-at-line-3192 pattern is replaced by an explicit parameter threaded from the caller. No stage can accidentally mutate the containment text because it is computed before any stage runs.
- **RFC-019 D0 (PictureResult shared references via list multiplication)**: The two-phase architecture constructs results in Phase B's output loop, one per crop, never via list multiplication. The current pattern of building `recovered`, `skip_reasons`, `retained_skips`, `clip_captures` as 4 parallel dicts that are merged at the end is replaced by a single list built from classifier output.
- **RFC-023 D1 (splice_figure_markers bail-out)**: With `RegionDisposition` carrying skip semantics, `bind_markers` knows which markers are deliberately empty vs failed, and can degrade per-marker instead of needing the landscape-fallback string filter as a proxy for "intentional skip."
- **RFC-035 D2 (landscape rasterize timeout)**: Not directly prevented by this restructuring (that is a timeout/rotation concern), but the extracted `_crop_page_region` helper is the single place to add a per-crop timeout guard.

**(4) Migration Risk and Sequencing**

Risk: The main risk is subtly changing gate evaluation order during extraction. The current coverage/clip-text/decorative gates have implicit ordering dependencies (coverage check runs before clip-text check; a region that passes coverage may still be skipped by clip-text).

Incremental sequence:
1. **Step 1 first (crop dedup)**: Pure mechanical refactor, zero behavior change, easy to verify with existing tests. Ship and stabilize.
2. **Step 2+3 together (extract classifier + rewrite loop)**: The classifier must produce identical decisions to the current interleaved gates. Validate by running the full corpus and diffing picture_results JSON output against a baseline snapshot. Add targeted unit tests for the classifier covering: scanned page with no text layer, garbled text layer, clip-text contained vs not, decorative icon, fullpage cap exceeded.
3. **Step 4 (inline _run_fallback_pipeline)**: Independent of steps 2-3. Low risk since the function is called once and the change is just moving the snapshot point to the caller.
4. **Step 5 last (RegionDisposition in splice)**: Depends on step 2 landing first. Low risk since bind_markers already handles mismatches gracefully.

Each step is independently shippable and revertable. Run the corpus scoring pipeline (`make ingest` + scoring) after each step to catch regressions.

**(5) Estimated Effort**

- Step 1 (crop dedup): 0.5 day
- Steps 2+3 (classifier extraction + loop rewrite): 2 days (includes corpus validation)
- Step 4 (inline _run_fallback_pipeline): 0.5 day
- Step 5 (RegionDisposition in splice): 0.5 day
- **Total: 3.5 days** including corpus regression testing between steps

Key files: `/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/src/pageindex_mcp/converters.py` (lines 2072-2381, 3162-3211), `/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/src/pageindex_mcp/picture_plane.py` (lines 133-223), `/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/src/pageindex_mcp/config.py` (lines 41-63).

---

### Zone 5: Verdict Persistence Dual-Path Inconsistency

**Severity:** high | **Bug count:** 6

**Description:** Verdict fields (verdict, verdict_reason, pipeline_version, verdict_computed_at, max_leaf_ratio) are persisted through two architecturally inconsistent paths. The tree path (_persist_tree_result at client.py:1936-2066) does a triple write: (1) save_doc with verdict embedded in artifact JSON, (2) write_verdict which re-reads the artifact, injects verdict fields, re-writes it, then calls save_doc_meta for the sidecar, (3) save_doc_meta again with non-verdict metadata. The flat path (_persist_flat_result at client.py:1755-1934) bypasses write_verdict entirely — save_flat_doc (storage.py:262-287) calls save_doc_meta directly with verdict fields embedded in the data dict. write_verdict's docstring (storage.py:662-676) declares it the "sole entry point for verdict mutation", but the flat path violates this contract. The safety net _verdict_cas_guard (storage.py:515-542) compares verdict_computed_at timestamps to prevent stale overwrites, but it only fires inside save_doc_meta — the artifact-level write in save_doc (storage.py:210-220) has no CAS protection. The registry dual-write (worker.py:690-731) is best-effort: it catches all exceptions and logs warnings, so a silent failure leaves Postgres stale until reconcile_registry_drift runs.

#### Mechanism
The dual-path creates bugs when any code assumes write_verdict is the universal entry point. The flat path's direct save_doc_meta call means: (1) promotion_sweep or recompute_verdicts calling write_verdict for a flat doc will re-read the flat artifact but write_verdict uses a content_class-dependent key selection (storage.py:690-693) that may pick the wrong artifact; (2) the tree path's triple write is redundant (artifact written twice, sidecar written twice) and creates a window where the artifact and sidecar carry different verdict values; (3) the registry dual-write's best-effort pattern means a document can be in MinIO with one verdict but in Postgres with another (or missing entirely), with no alert distinguishing "never wrote" from "wrote stale data". The _confirm_write_visible barrier (storage.py:37-65, now 0.45s worst-case) adds latency that caused the Arabic SLA doc to land 3-5 minutes late (RFC-036 D1), but removing it risks read-after-write inconsistency.

#### History
a. RFC-034 D18: write-visibility barrier (4.4s/8.8s worst-case) caused Arabic SLA doc to land 3-5 minutes late.
b. RFC-036 D1: reduced barrier to 0.45s, added PersistenceNotVisibleError handling.
c. RFC-034 D19: density-guarded OCR preservation staged in git but never committed, inactive during Run 19.
d. RFC-006/RFC-009 D6: registry dual-write removed fallback, one legacy doc failure leaves registry:complete unset.
e. Run 16-19 verdict-correction audits: verdict labels drifted independently of persisted content (Haftpflicht FAIL->PASS with empty verdict_reason on identical tree).

#### Code Evidence
`client.py:1972-1989` save_doc (first artifact write). `client.py:1991-1999` write_verdict (re-reads+re-writes artifact, writes sidecar). `client.py:2043` save_doc_meta (second sidecar write). `client.py:1903` save_flat_doc (flat path bypasses write_verdict). `storage.py:262-287` save_flat_doc -> save_doc_meta at line 287. `storage.py:653-676` write_verdict docstring ('sole entry point'). `storage.py:515-542` _verdict_cas_guard. `worker.py:707-731` _upsert_registry_row (best-effort, catches all exceptions).

#### Key Files
- src/pageindex_mcp/storage.py
- src/pageindex_mcp/client.py
- src/pageindex_mcp/worker.py

#### Simplification Proposal
**(1) Core simplification (2-3 sentences)**

Eliminate `write_verdict` entirely. Make `save_doc_meta` the single writer for verdict fields (it already has the CAS guard), and stop embedding verdict fields inside the artifact JSON (`processed/<id>.json` / `processed/<id>.flat.json`). The artifact stores content (structure/blocks); the sidecar stores all metadata including verdict. Both persist paths — tree and flat — call `save_doc_meta` exactly once with the full field set (verdict + non-verdict), replacing the tree path's current triple-write (save_doc with verdict, write_verdict re-read+re-write, save_doc_meta again) and the flat path's implicit verdict-via-data-dict.

**(2) Concrete restructuring steps**

Step A — Remove verdict fields from artifact writes (client.py, ~-12 lines):
In `_persist_tree_result` (client.py:1972-1989), remove `verdict`, `verdict_reason`, `max_leaf_ratio`, `pipeline_version`, `verdict_computed_at` from the dict passed to `save_doc`. These fields stay only in the sidecar. Net: -5 lines from the save_doc dict.

Step B — Delete the write_verdict call in the tree path (client.py:1991-1999, ~-9 lines):
Remove `await asyncio.to_thread(write_verdict, ...)`. This eliminates the artifact re-read+re-write+second _confirm_write_visible round-trip.

Step C — Merge verdict fields into the existing save_doc_meta call (client.py:2000-2043, ~+6 lines):
Add verdict, verdict_reason, pipeline_version, verdict_computed_at, max_leaf_ratio to the `meta` dict already being built at line 2000. The single `save_doc_meta` call at line 2043 then carries everything. Net change in this block: +6 lines.

Step D — Delete `write_verdict` function from storage.py (lines 653-741, ~-89 lines):
The function, its docstring, and the artifact-level re-read+re-write logic all go. The `_verdict_cas_guard` and `save_doc_meta` remain unchanged — they already do the correct merge.

Step E — Update callers of write_verdict outside ingest (grep found none beyond client.py:1992 and the import at client.py:104):
Remove the import. If `promotion_sweep` or `recompute_verdicts` exist as future callers, they should call `save_doc_meta` directly with the verdict fields dict — the CAS guard protects against stale overwrites.

Step F — Flatten the flat path's implicit contract (storage.py:262-287, ~0 lines changed):
`save_flat_doc` already calls `save_doc_meta(doc_id, data)` at line 287, and the flat artifact does not need verdict fields either — same treatment as the tree artifact. Remove verdict fields from `flat_meta` dict in `_persist_flat_result` (client.py:1883-1899) and add them to a separate sidecar-only meta dict, or (simpler) leave them in since `save_flat_doc` passes the whole dict to `save_doc_meta` which merges correctly. The key change: document that `save_doc_meta` is the verdict writer for both paths, and remove verdict fields from the artifact portion of flat_meta written to `processed/<id>.flat.json`.

Step G — Add CAS guard to save_doc if verdict fields remain in artifacts (storage.py:205-226):
If verdict fields are stripped from artifacts (recommended), this step is unnecessary. If they stay for backward compatibility, add `_verdict_cas_guard` to `save_doc` to close the unguarded-write gap.

Rough line-count delta: -90 to -100 lines net (write_verdict deletion dominates).

Files touched: `src/pageindex_mcp/storage.py` (-89 lines), `src/pageindex_mcp/client.py` (-20 lines), `src/pageindex_mcp/registry_backfill.py` (docstring-only, ~0).

**(3) Historical bug classes this would have prevented**

- Haftpflicht FAIL->PASS drift (Run 16-19): verdict in artifact and sidecar could carry different values during the triple-write window; a single writer eliminates the divergence window entirely.
- Registry stale-verdict (worker.py best-effort): the `verdict_fields` overlay at worker.py:716-720 exists precisely because the artifact might not yet reflect the verdict due to write_verdict's timing. With save_doc_meta as sole writer, the overlay is still useful for the read-after-write race, but the source is now authoritative rather than a compensating patch.
- promotion_sweep / recompute_verdicts picking the wrong artifact key (storage.py:690-693 content_class branching): eliminated because write_verdict no longer exists — all verdict mutation goes through save_doc_meta which is content_class-agnostic (it always writes the same `processed/<id>.meta.json` sidecar).
- RFC-036 D1 latency (Arabic SLA doc 3-5 min late): removing write_verdict eliminates one full _confirm_write_visible round-trip (0.45s) from the tree path, cutting total barrier time roughly in half.

**(4) Migration risk and incremental sequencing**

Risk: Any code that reads verdict fields from the artifact JSON (`processed/<id>.json`) rather than the sidecar will break. Mitigation: grep for `get_doc` + verdict field access; the main consumers are `read_registry_fields` (storage.py, which already reads the sidecar first) and the MCP query tools (which read structure, not verdict).

Incremental sequence:
1. First commit: add verdict fields to the tree path's `save_doc_meta` call (Step C) so the sidecar is authoritative. Keep write_verdict alive but log a deprecation warning. This is pure additive, zero risk.
2. Second commit: remove write_verdict call from `_persist_tree_result` (Step B) and delete import. The sidecar now carries the verdict; the artifact still has verdict fields from save_doc but they are no longer refreshed by write_verdict. Test: verify sidecar verdict matches expected values for tree and flat docs.
3. Third commit: strip verdict fields from the artifact dicts in both persist paths (Steps A, F). This is the breaking change for any artifact-verdict readers. Run full corpus ingest+score cycle to verify no consumer regresses.
4. Fourth commit: delete write_verdict function (Step D). Pure deletion.

Each commit is independently deployable and rollback-safe. The backward-compatible period (commits 1-2) lets you verify no hidden artifact-verdict readers exist before committing to the removal.

**(5) Estimated effort**

2-3 hours of code changes (mostly deletion). 1-2 hours of corpus spot-check (ingest 3-5 docs across tree/flat paths, verify sidecar + registry consistency). Total: half a day, including the incremental 4-commit sequencing.

---

### Zone 6: Arabic/RTL Pipeline Bolt-On Architecture

**Severity:** high | **Bug count:** 9

**Description:** The pipeline's core abstractions (heading extraction, tree building, garble detection, content measurement) were designed for Latin-script documents and Arabic/RTL support is layered on via a series of bolt-on fixes that interact destructively. reconstruct_bidi_order runs inside _pre_inference_normalize (converters.py:1444) on raw markdown BEFORE tree building, but validate_tree (helpers.py:1832) calls decide_rtl again on the tree's flattened text AFTER tree building — the two calls operate on different text and may produce different RTL decisions. The NFKC normalization (converters.py:2357) decomposes Arabic Presentation Forms (U+FB50-FEFF) to base Arabic (U+0600-06FF) before detectors that test for presentation-form Unicode names ever see them. The D3 re-normalization safety net (client.py ~line 919, REMOTE_MD_RENORMALIZE) applies bidi reordering to all remote-returned markdown, which interacts badly with heading/block-boundary detection on mixed-script content (MOU MOHRE collapsed 134 nodes->20 with garbled Latin). A heading-order guard (_heading_is_logical_order) was written locally but never deployed to the remote Docling service, so the remote service does an unconditional heading flip with no table repair.

#### Mechanism
Each Arabic fix operates at a different pipeline stage (pre-normalization, post-normalization, garble detection, tree validation, verdict classification) and on a different text representation (raw markdown, NFKC-normalized markdown, tree node text, flattened tree text). A fix at one stage (e.g. NFKC normalization decomposes presentation forms) destroys the signal a detector at another stage depends on (e.g. _word_has_reversed_morphology originally tested presentation-form Unicode names). The bidi reordering runs twice (once in converters for markdown, once via decide_rtl in helpers for tree text) and may produce contradictory results on the same document. The BiDi early-return optimization (converters.py:1249-1265, Arabic ratio <= 15%) bypasses heading repair for low-Arabic bilingual docs, leaving scrambled headings for the tree builder. The remote vs local split means fixes deployed to local converters.py are invisible to the remote Docling service.

#### History
a. RFC-033 D2/RFC-034 D7: _reversed_morphology tested presentation-form Unicode names but NFKC decomposed them all to base Arabic — 0% TPR, 24/26 reversed titles missed.
b. RFC-034 D3/D17: D3 re-normalization caused MOU MOHRE collapse 134->20 nodes with garbled Latin.
c. RFC-023 D9: bidi early-return skipped heading repair for bilingual docs, producing flat tree.
d. RFC-033 F1: heading-order guard uncommitted/never deployed to remote service, all 23 headings of siyasat-hawkama corrupted.
e. RFC-020 F2/RFC-021 QF1: expected_script threading forced OCR on Arabic PDFs, destroying PictureItem segmentation.
f. RFC-020 D3: Arabic OCR language override only partially fixed Latin-in-Arabic mojibake.

#### Code Evidence
`helpers.py:1832` validate_tree calls decide_rtl on flat_text. `converters.py:1444` reconstruct_bidi_order in _pre_inference_normalize. `script.py:242-263` _word_has_reversed_morphology (joining-type based, replaces presentation-form check). `helpers.py:1457` comment confirming _check_bidi_coherence deleted. `helpers.py:1654-1701` gates 6+7 (RTL_REVERSAL + BIDI_DEGRADED) sharing same decide_rtl call. `config.py:27` REMOTE_MD_RENORMALIZE flag. `converters.py:1249-1265` bidi early-return optimization.

#### Key Files
- src/pageindex_mcp/converters.py
- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/script.py
- src/pageindex_mcp/client.py

#### Simplification Proposal
_No dedicated proposal was generated for this zone in the source data. The recommended near-term step is to consolidate the two independent decide_rtl/bidi call sites (converters.py:1444 pre-tree-build, helpers.py:1832 post-tree-build) into a single RTL decision computed once and threaded through, and to deploy the local `_heading_is_logical_order` guard to the remote Docling service so local and remote heading repair stay in sync._

---

### Zone 7: God Function Orchestration (pdf_to_markdown_docling)

**Severity:** medium | **Bug count:** 6

**Description:** pdf_to_markdown_docling (converters.py:3214-3547, ~333 lines) is a monolithic orchestrator that handles: Docling conversion, AGPL page-count guard, landscape page detection + rasterize-rotate-reextract fallback, rotation normalization, hierarchical add-on with monkey-patching (_patch_hierarchical_infer), heading re-promotion, raw vs post candidate selection based on structural depth, the full _run_fallback_pipeline (normalize_indented_headings -> body_for_containment snapshot -> document_level_text_fallback -> splice_landscape_fallback), picture recovery via _recover_picture_results, and provenance recording. The candidate selection gate at line 3470-3481 picks between post-add-on and raw Docling markdown based on structural depth, but both candidates independently run the full heading-depth recovery chain, and the selection always prefers post when both pass. The _run_fallback_pipeline's stage ordering is explicitly documented as 'load-bearing' (converters.py:3172) — moving the body_for_containment snapshot relative to _document_level_text_fallback breaks containment checks.

#### Mechanism
The function's size and multi-responsibility scope means any change to one concern risks breaking another. The landscape fallback (converters.py:3296-3341) triggers a serial page loop bounded by MAX_LANDSCAPE_PAGES and a deadline, but the serial nature caused world-stats-pocketbook (292 pages) to timeout. The monkey-patching of HierarchyBuilderMetadata.infer (converters.py:1260-1374) is fingerprint-guarded against upstream drift but couples tightly to a third-party library's internals. The post vs raw candidate selection (line 3470-3481) has no fallback when both fail depth: it silently uses the post candidate even when raw might have better content. Because picture recovery runs last (line 3510), its containment check depends on all prior stages' cumulative text mutations — adding a new stage between _run_fallback_pipeline and _recover_picture_results would silently break containment.

#### History
a. RFC-035 D2: landscape rasterize-rotate-reextract caused three compounding defects — uncapped serial page loop exhausted 1500s timeout, non-daemon ThreadPoolExecutor threads survived timeout, chart axis labels fragmented by _segment_table_nodes.
b. RFC-024 D1: _document_level_text_fallback appended text before containment check, suppressing picture recovery (fixed by body_for_containment snapshot).
c. RFC-034 D11: _strip_toc_heading_nodes over-stripped without depth guard, Penal Code regressed PASS->MARGINAL (493/595 nodes flattened).
d. RFC-034 D16: added depth/node-count guard to D11.

#### Code Evidence
`converters.py:3214-3547` pdf_to_markdown_docling (333 lines). `converters.py:3162-3211` _run_fallback_pipeline (body_for_containment snapshot at 3192). `converters.py:3470-3481` post vs raw candidate selection. `converters.py:3296-3341` landscape fallback trigger. `converters.py:1260-1374` _patch_hierarchical_infer monkey-patch. `helpers.py:3291-3310` _strip_toc_heading_nodes_guarded (D16 fix for D11 over-stripping).

#### Key Files
- src/pageindex_mcp/converters.py
- src/pageindex_mcp/helpers.py

#### Simplification Proposal
_No dedicated proposal was generated for this zone in the source data. The recommended near-term step is to split pdf_to_markdown_docling's 333 lines into named stages (convert, landscape-fallback, hierarchical-add-on, candidate-selection, fallback-pipeline, picture-recovery) each independently testable, and to give the post-vs-raw candidate selection an explicit fallback when both fail depth instead of silently preferring post._

---

### Zone 8: Env-Var Flag Proliferation Without Interaction Registry

**Severity:** medium | **Bug count:** 5

**Description:** At least 30+ environment variables control distinct behaviors across config.py (66 os.environ reads), converters.py (29 reads), helpers.py (21 reads), and client.py (16 reads), with no centralized documentation of their interactions. Several flags have non-obvious cascading effects: ALLOW_AGPL_FALLBACK=false disables picture OCR recovery, landscape fallback, rotation normalization, chunked Docling, and fitz rasterization simultaneously (checked at converters.py:1831, 1879, 2119, 2990, 3259, 3593). OCR_ESCALATION_GARBLE and OCR_ESCALATION_PER_PICTURE default to the master OCR_ESCALATION value when not independently set (config.py:41-59), creating implicit coupling. _TEXT_LAYER_GARBLE_CHECK_ENABLED affects _text_layer_has_content which affects coverage exemption which affects image_enrichment_ratio which affects classify_verdict's rescue path — a four-hop transitive dependency. VerdictThresholds are cached at first call (helpers.py:331) and never refreshed until reset_verdict_thresholds() is called, so env var changes mid-process are invisible. PDF_INSPECTOR_PRECLASSIFY is checked in three separate locations (client.py:996, worker.py:336, client.py:2035) with no single enforcement point.

#### Mechanism
When a fix requires a new behavioral gate, a new env var is added in isolation without documenting its interaction with existing flags. Operators setting one flag (e.g. OCR_ESCALATION=false) may not realize it implicitly disables two sub-flags. A partial deployment where PDF_INSPECTOR_PRECLASSIFY=1 is set but pdf-inspector is not installed results in None classification that silently disables OCR forcing, timeout multiplier, and sidecar metadata — three separate silent failures from one misconfiguration. The VerdictThresholds cache means threshold changes (PASS_MAX_LEAF_RATIO, GARBLE_WINDOW_RATIO_THRESHOLD) made via env var after process start have no effect until a process restart, but no documentation warns of this. The ALLOW_AGPL_FALLBACK flag gates so many disparate features that setting it to false for AGPL compliance also disables unrelated functionality (landscape extraction, picture recovery), with no granular control.

#### History
a. RFC-032: PDF_INSPECTOR_PRECLASSIFY flag defined but dead-ends (classification computed/logged, never used for routing).
b. Dockerfile fix May 2026: missing libgl1+libglib2.0-0 caused Docling ImportError, silent fallback to pymupdf4llm, misattributed as document-quality failure.
c. RFC-033 F1 C-2: remote Docling 504s silently fall through to pymupdf4llm (AGPL) with only logger.warning, no hard gate.
d. RFC-024 D0/D10: PASS_MAX_LEAF_RATIO widened repeatedly (0.17->0.20->0.30) chasing non-determinism, each threshold a separate env var read.

#### Code Evidence
`config.py:21-23` PDF_INSPECTOR_PRECLASSIFY (default '0'). `config.py:41-59` OCR_ESCALATION->OCR_ESCALATION_GARBLE/OCR_ESCALATION_PER_PICTURE coupling. `helpers.py:331` VerdictThresholds module-level cache. `converters.py:3256-3265` ALLOW_AGPL_FALLBACK gating 6+ features. `converters.py:3563-3593` pdf_markdown_converters chain composition. `helpers.py:1698` BIDI_COHERENCE_ENFORCE inline os.environ.get. `helpers.py:1370-1371` GARBLE_SHORT_TEXT_DEFAULT/GARBLE_FLAT_MARKDOWN_NORMALIZE inline reads.

#### Key Files
- src/pageindex_mcp/config.py
- src/pageindex_mcp/converters.py
- src/pageindex_mcp/helpers.py
- src/pageindex_mcp/client.py

#### Simplification Proposal
_No dedicated proposal was generated for this zone in the source data. The recommended near-term step is to build a single env-var interaction registry (a table of flag -> dependent behaviors -> default-inheritance rules) validated by a startup-time consistency check, and to make VerdictThresholds either eagerly refresh on env change or fail loudly if read after a mid-process mutation._

## Cross-Cutting Themes

- Incomplete feature decoupling across divergent pipeline paths: OCR/enrichment logic split between tree-path and flat-path (or page-level vs per-picture) without keeping both in sync, so a fix or optimization on one path silently breaks or conflates behavior on the other (per-picture OCR splice removed from tree path; page-level and per-picture OCR sharing one _OCR_ESCALATION flag).
- Missing or unpropagated context/parameters across call chains: expected_script, verdict reason codes, content_class, and prior_verdict repeatedly fail to reach the function that needs them to make a correct decision, silently disabling the very detection logic that was added to catch a specific failure mode (Latin-gibberish check gated on expected_script; OCR escalation gated on the literal string 'garbling' instead of structural reasons; classify_verdict lacking expected_script).
- Upstream filters/gates fire without communicating intent, and downstream handlers can't distinguish 'deliberately skipped' from 'genuinely failed': page-coverage filters unconditionally suppress content recovery even with no text layer; count-mismatch guards bail out entirely instead of degrading gracefully; later-stage overwrites silently clobber earlier routing decisions.
- Gate/promotion ordering and precondition bugs: hard-FAIL checks fire before promotion/rescue logic can run; ToC-stripping and other per-node heuristics apply with no document-level bound (depth guard, node-count threshold), causing correct-on-small-docs logic to catastrophically over-strip large legal statutes.
- Detector/signal design mismatches with upstream transformations: NFKC normalization decomposes presentation-form Arabic before a detector that only recognizes presentation-form codepoints ever sees it; a codepoint-range line selector excludes the exact block where the failure signal lives; PUA-only garble detection misses RTL word-splitting, presentation-forms encoding, and Latin-in-Arabic mojibake that use valid (non-PUA) codepoints — new detectors keep exposing the next blind spot at each audit cycle.
- Threshold tuning applied repeatedly to threshold-shaped problems that are actually non-determinism: PASS_MAX_LEAF_RATIO widened 0.17→0.20→0.30 (and again independently 0.17→0.30 with hysteresis) chasing Docling's ML-based non-deterministic heading selection, each widening trading false negatives for new false positives rather than fixing the underlying jitter.
- Dead code and unwired features: functions fully implemented (chunked_docling_timeout_s, _check_bidi_coherence, PDF_INSPECTOR_PRECLASSIFY classification, D19 OCR-density guard staged-but-uncommitted, judge calibration rules) never get called, imported, branched on, or committed, so their intended fix has zero effect in production despite appearing 'done'.
- Fire-and-forget and swallow-error patterns creating silent partial failures: async registry deletes in the erasure cascade, non-atomic dual-writes between MinIO and Postgres registry, and non-daemon executor threads surviving timeouts all report success or 'complete' while the underlying operation partially failed.
- Measurement/accounting divergence masking real content loss: meta character counters, verdict labels, and persisted block content drift independently of each other (15x divergence in Unfallversicherung, 67% char drop on stable-verdict world-stats-pocketbook, table blocks measured via a 'text' key they don't have), so quality gates and audits can be fooled by counters that no longer reflect what was actually persisted.
- Single points of failure in shared fallback dependencies: Tesseract and VLM OCR fallbacks share the same pypdfium2 rasterizer, so the one failure mode (CMap corruption) takes down both recovery paths simultaneously; recovery logic nested inside one exception handler is unreachable from a sibling failure mode (VLM succeeds but produces garbled output).
- Compounding fix cycles on Arabic/RTL and bilingual content: nearly every fix category (garble detection, coverage filters, script threading, bidi reordering, ToC stripping, write-visibility, landscape extraction) recurs specifically on Arabic-script, RTL, or bilingual documents, indicating the pipeline's core abstractions were designed Latin-first and each Arabic-specific patch surfaces a new interaction bug rather than resolving the class of defect.
