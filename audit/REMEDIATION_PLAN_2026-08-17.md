# Remediation Plan — 2026-08-17

**Audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-17_POST-FIX-4.md
**Zones:** 2 of 8 (top by priority)
**Waves:** 2

## Priority Scores

| Zone | Severity | Bug Count | Score | Proposal Status | Excluded |
|---|---|---|---|---|---|
| Zone 1: Garble Detection Hydra | critical | 12 | 57.6 | no_proposal | no |
| Zone 2: God Function Routing Cascade (client.py index()) | critical | 11 | 52.8 | no_proposal | no |
| Zone 5: OCR/Enrichment Signal Conflation | high | 9 | 32.4 | no_proposal | no |
| Zone 4: Threshold Calibration Feedback Loops | high | 8 | 28.8 | no_proposal | no |
| Zone 3: Verdict Persistence Split-Brain | high | 7 | 25.2 | no_proposal | no |
| Zone 6: Conversion Pipeline Stage Coupling (pdf_to_markdown_docling) | high | 7 | 25.2 | no_proposal | no |
| Zone 7: Registry/Persistence Consistency Gaps | medium | 6 | 14.4 | no_proposal | no |
| Zone 8: Dead/Uncommitted/Stale Code Divergence | medium | 6 | 14.4 | no_proposal | no |

Score formula: severity_weight (critical=4, high=3, medium=2) × bug_count × 1.2 (no-delta default multiplier, applied because no delta object confirmed landed/wired status for any zone).

This plan carries forward only the top 2 zones by score (Zone 1, Zone 2). The remaining 6 zones (scores 32.4 down to 14.4) are queued but out of scope for this remediation cycle.

## Wave Sequence

### Wave 1 — Zone 1: Garble Detection Hydra

**Rationale (validated correction applied — see Validation Results below):** Zone 1's originally-specified helpers.py consolidation (unified `check_garble()` / `GarbleContext` replacing 5+ scattered garble sites) is **already landed** in commits `33cc1f5` and `113c33a` on this branch. The residual Zone 1 work is narrower than the original spec claimed: threading `expected_script` through the primary `pdf_markdown_converters()` chain in `client.py` and `converters.py` only. Validation further found this residual work **already implemented, uncommitted, in the working tree** (`git status` shows `client.py` and `converters.py` modified; `tests/test_client_contract.py`, `tests/test_imgblock_audit_findings.py`, `tests/test_rfc021_qf1.py`, `tests/test_rfc027_d7.py`, `tests/test_rfc028_d5.py`, `tests/test_vlm_fallback.py` modified; `tests/test_zone1_chain_expected_script.py` present but untracked). Wave 1 is therefore rescoped from "implement" to **"review, verify wiring, run tests, and commit the existing working-tree diff"** — not fresh implementation. `helpers.py` is not a Zone 1 shared file for this cycle (it was in the original mis-scoped rationale; corrected here).

**Shared files (wave-internal, working-tree state):** `src/pageindex_mcp/client.py`, `src/pageindex_mcp/converters.py`

### Wave 2 — Zone 2: God Function Routing Cascade (client.py index())

**Rationale (validated correction applied):** Zone 2 depends on Wave 1 only through the shared file `client.py` (Wave 1's diff touches the conversion-chain invocation sites; Zone 2's dispatch redesign touches the routing block later in the same file) — sequencing avoids a merge conflict inside `client.py`, not an API dependency. The original rationale's claim that Zone 2's recovery methods need Zone 1's `check_garble()` API is stale: those recovery methods already exist on this branch and already call `check_garble()` (landed in the same prior commits as Zone 1's consolidation). Zone 2's actual code targets contain no garble-detection work at all — they are purely about exhaustive route dispatch in `index()`. Wave 2 must **not** proceed until the Zone 2 spec corrections below (from Validation Results) are applied — the spec as originally written contains a blocker-severity factual error about `state.ok`/`Route.FLAT` semantics that would change behavior if implemented literally.

**Shared files:** none (no file overlap with Zone 1's Wave 1 diff outside `client.py`, and Wave 1 will be committed before Wave 2 begins).

## Fix Specs

### Zone: Zone 1: Garble Detection Hydra (wave 1, priority 1)

**Mechanism to eliminate:** The primary PDF conversion chain (`pdf_markdown_converters()`) stores converter callables that `client.py` invokes without passing `expected_script`. Every first-pass local Docling conversion runs `converters.py`-internal garble checks (`_text_layer_has_content`, `_document_level_text_fallback`, region-level check in `_recover_picture_text`) with `expected_script=None`, causing a fallback to `infer_script(text)` on the text being checked. An Arabic PDF whose text layer contains Latin garbage infers `expected_script="Latn"`, bypassing the `latin_gibberish` prong entirely. The core garble consolidation (deleting `_tree_is_garbled`/`_flat_text_is_garbled`, creating `check_garble` with `GarbleContext` enum) already landed in commits `33cc1f5` and `113c33a`. The residual gap is specific to the primary first-pass conversion path — force-OCR-escalation retry paths already pass `expected_script` correctly.

**Strategy:** Thread `expected_script` through the `pdf_markdown_converters()` chain: (1) chain callable type annotation widened from `Callable[[str], ...]` to `Callable[..., ...]`; (2) `_pdf_to_markdown_no_pics` accepts `**kwargs` to absorb `expected_script` it does not use; (3) `expected_script=expected_script` passed at both chain invocation sites in `client.py` `_convert_to_tree` (docling full-page-OCR branch and general else branch); (4) remote Docling gap documented as a known limitation (server-side contract change, out of scope for client-side threading).

**Code targets:**

| File | Lines (working tree) | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/client.py` | ~1068 (docling full-page-OCR conv_fn call) | Pass `expected_script` through the docling full-page-OCR chain callable invocation | Add `expected_script=expected_script` as an additional keyword argument to the existing `conv_fn(file_path, True, ocr_lang_override=detect_ocr_langs(filename))` call | Must not change the positional args or `ocr_lang_override` kwarg; must preserve `_split_converter_output` wrapper and `stages_out` capture |
| `src/pageindex_mcp/client.py` | ~1086 (general-branch conv_fn call) | Pass `expected_script` through the general (non-full-page-OCR) chain callable invocation | Add `expected_script=expected_script` as a keyword argument to the `conv_fn(file_path)` call in the else branch of the `ocr_mode==FULL_PAGE` conditional | Must not break the pymupdf4llm route; must preserve chain-iteration fallback semantics (conv_fn raises → loop continues to next converter) |
| `src/pageindex_mcp/client.py` | ~1035 (top of remote Docling branch) | Document remote Docling gap as a known limitation | Comment block after `if state.use_remote and "docling" in conv_name:` explaining `_remote_pdf_to_markdown` does not forward `expected_script` to the external Docling microservice (no script field in `/convert/pdf` payload), so server-side garble checks fall back to `infer_script(text)`; post-conversion garble detection still receives `expected_script` from the caller | Comment-only, no behavioral change |
| `src/pageindex_mcp/converters.py` | ~3496-3497 | `_pdf_to_markdown_no_pics` accepts and ignores `expected_script` via `**kwargs` | Signature `_pdf_to_markdown_no_pics(pdf_path: str, **kwargs: object)`; docstring explains `**kwargs` absorbs chain-level keyword arguments the pymupdf4llm backend has no use for | Must not change return value or `pdf_to_markdown` call; must not introduce any use of `expected_script` |
| `src/pageindex_mcp/converters.py` | ~3509 | Update `pdf_markdown_converters()` chain type annotation to accept keyword arguments | Return type annotation widened to `Callable[..., tuple[str, list[PictureResult], dict[str, dict]]]` for both the function return type and chain variable annotation; docstring documents every chain callable accepts `(pdf_path: str, **kwargs)` minimum | Must not change chain construction logic or converter ordering |

**Wiring checks:**

| Symbol | Must be imported/called by | Check type |
|---|---|---|
| `pdf_to_markdown_docling(expected_script=...)` | `src/pageindex_mcp/client.py` | call |
| `pdf_markdown_converters()` chain invocation with `expected_script` kwarg (both call sites) | `src/pageindex_mcp/client.py` | call |
| `_pdf_to_markdown_no_pics(**kwargs)` acceptance verified via unit call, not import self-reference (corrected per Validation Results — see issue below) | `tests/test_zone1_chain_expected_script.py` | contract |

**Test requirements:**

- `tests/test_zone1_chain_expected_script.py` — AST-parse `client.py` to confirm both chain-iteration `conv_fn` call sites pass `expected_script` as a keyword argument; call `_pdf_to_markdown_no_pics` with `expected_script='Arab'` and `expected_script=None`, assert identical output (assertion_type: wiring)
- `tests/test_zone1_chain_expected_script.py` — Contract test: mock inner `check_garble` calls inside `_text_layer_has_content`, `_document_level_text_fallback`, and the region garble check; call `pdf_to_markdown_docling(expected_script='Arab')`; verify `check_garble` receives `expected_script='Arab'` (not None, not re-inferred) at all three sites (assertion_type: contract)
- `tests/test_zone1_chain_expected_script.py` — Regression test: `_pdf_to_markdown_no_pics` with/without `expected_script` keyword produces identical output (assertion_type: regression)
- `tests/test_zone1_garble_wiring.py` — Extend existing AST-based wiring test to cover the primary chain path at both conv_fn invocation sites, not just the escalation paths (assertion_type: wiring)

**Corpus validation:** affected documents warid-597, MOU, qarar-106, arabicSLA, cabinet_resolution_no_96, Haftpflicht, siyasat-hawkama, marsoom-13; expected verdict direction: improve; spot-check count: 8.

**Estimated complexity:** small.

---

### Zone: Zone 2: God Function Routing Cascade (client.py index()) (wave 2, priority 2)

**Mechanism to eliminate:** Non-exhaustive route dispatch in the `index()` orchestrator: the post-recovery routing decision checks only `Route.FLAT` and `Route.REJECT` explicitly, letting `Route.PERSIST_FAIL` and `Route.TREE` fall through implicitly to `_persist_tree_result()`. Adding a new `Route` value silently falls through. `state.first_defect` is computed once in `_convert_to_tree` (~line 1254) and never recomputed after recovery methods change `state.ok`/`gate_result`/`reason`, so the routing decision (~lines 2110-2148) uses stale defect information. Recovery methods override `state.route` to `Route.FLAT` directly in 5 places, bypassing `decide_route()`, creating ad-hoc routing that can diverge from `REASON_POLICY`. This is the residual bug class after commit `646cdc0` decomposed the 1409-line `index()` into extracted methods.

**Strategy:** Type-safe exhaustive route dispatch. Replace the implicit if/elif fallthrough in `index()` with an exhaustive `match`/`case` on the `Route` enum that explicitly handles every reachable state combination. Add a `_finalize_routing()` method that recomputes `first_defect` and `route` from post-recovery `gate_result` via `decide_route()`, called once after the recovery pipeline completes.

**⚠️ This spec must not be implemented as originally written.** Validation (see Validation Results) found a blocker-severity factual error and three major-severity gaps. The corrected code targets below fold in the required fixes; do not use the pre-correction case enumeration.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/client.py` | ~2095-2108 | Add `_finalize_routing()` call after recovery pipeline completes | After the 7 recovery method calls, insert a call to `_finalize_routing(state)` that: (1) recomputes `state.first_defect` from `state.gate_result` if updated by recovery, (2) recomputes `state.route` via `decide_route(state.first_defect)` **gated on `state.route_overridden` (new explicit flag — see helpers.py target below), not on `state.route == Route.FLAT`**, (3) updates `state.total_chars` from the current tree | Recovery methods that explicitly set `state.route = Route.FLAT` must take precedence over `decide_route()` recomputation, detected via the explicit flag, not by inferring override from the route value (the original spec's `route == Route.FLAT` check is unsound because `decide_route()` at ~line 1259 can itself legitimately return `Route.FLAT` on first pass, e.g. `NODE_COUNT_LOW` with flat-doc-routing on) |
| `src/pageindex_mcp/client.py` | ~2110-2160 | Replace implicit if/elif route dispatch with exhaustive match/case | Match on `(state.ok, state.route)` and **also** on `state.flat_garble_unrecovered` as an explicit guard evaluated before the match (do not drop it — see corrected case table below) | The semantic outcome for every existing reachable `(ok, route)` combination — including `(False, Route.TREE)`, which today reaches the "persist low-quality tree with FAIL verdict" path by fallthrough — must remain identical to current behavior. No bare `case _`; use `assert_never` only after every reachable combination (confirmed via code reading, not assumption) has an explicit case |
| `src/pageindex_mcp/client.py` | ~915-918 | Add `_finalize_routing()` method to `CustomPageIndexClient` | New ~20-line method: `def _finalize_routing(self, state: ExtractionState) -> None`. If `state.route_overridden` is True, skip `decide_route` recomputation. Otherwise recompute `state.first_defect` from `state.gate_result.defect` (or `_defect_from_reason_str(state.reason)` if `gate_result` is None), set `state.route = decide_route(state.first_defect, settings.flat_doc_routing)`, update `state.total_chars` | Must not change `state.first_defect` if recovery already succeeded (`state.ok` True, route already `TREE`); must preserve `ExtractionSnapshot` restore semantics in `_recover_ocr_escalation` |
| `src/pageindex_mcp/client.py` | 5 recovery-override sites (~1502, 1556, 1572, 1684, 1714) | Set `state.route_overridden = True` at every site that directly overrides `state.route` | Add the flag assignment alongside each existing `state.route = Route.FLAT` (or other) override, including the flat-prefer (~1683-1684) and landscape-reroute (~1713-1714) sites, which set `state.ok = False` with `Route.FLAT` — **not** `state.ok = True` as the original spec incorrectly claimed | Flag must be added at every override site, not a subset, or `_finalize_routing` will incorrectly recompute route for an unflagged override |
| `src/pageindex_mcp/helpers.py` | after `Route` class definition | Add `route_overridden: bool` field to `ExtractionState` (replaces the originally-proposed `ROUTE_VALUES` frozenset, which was a test-only symbol with no production consumer) | Add `route_overridden: bool = False` to the `ExtractionState` dataclass | Default `False`; only recovery-override sites set it `True` |

**Corrected dispatch case table** (replaces the original spec's 6-case enumeration, which was missing `(False, Route.TREE)` and mis-stated flat-prefer/landscape-reroute as `state.ok=True`):

1. Guard clause (pre-match): if `state.flat_garble_unrecovered` → reject path (raise `LowQualityTreeError`), matching current behavior at ~2126-2135 where this is an orthogonal OR-condition, not folded into route.
2. `case (True, Route.TREE)` → `_persist_tree_result` (success)
3. `case (False, Route.TREE)` → `_persist_tree_result` with FAIL verdict (current fallthrough behavior — must be made explicit, not left to crash under `assert_never`)
4. `case (False, Route.FLAT)` → `_persist_flat_result`, reject on failure (covers flat-prefer and landscape-reroute, both of which set `ok=False`)
5. `case (_, Route.REJECT)` → raise `LowQualityTreeError`
6. `case (_, Route.PERSIST_FAIL)` → log and `_persist_tree_result` with FAIL verdict
7. `case (True, Route.FLAT)` → resolve explicitly against current behavior before coding (original spec assumed this case is used by flat-prefer/landscape-reroute; it is not — verify whether this combination is reachable at all post-`_finalize_routing`, and either give it an explicit outcome backed by a passing regression test, or prove and test its unreachability — do not leave it to `assert_never` without that proof)

**Wiring checks:**

| Symbol | Must be imported/called by | Check type |
|---|---|---|
| `_finalize_routing` | `src/pageindex_mcp/client.py` | call |
| `decide_route` | `src/pageindex_mcp/client.py` | call |
| `Route.PERSIST_FAIL` | `src/pageindex_mcp/client.py` | dispatch |
| `state.route_overridden` | `src/pageindex_mcp/client.py` (all 5 override sites) | assignment coverage |

**Test requirements:**

- `tests/test_zone2_route_exhaustiveness.py` — parametrize over all reachable `(state.ok, state.route)` combinations found by code reading (including `(False, Route.TREE)`), verify each produces the expected outcome class; verify the dispatch has no wildcard default (assertion_type: exhaustiveness)
- `tests/test_zone2_finalize_routing.py` — `_finalize_routing` recomputes `first_defect`/`route` correctly from post-recovery `gate_result`. Test cases re-derived from actual `ExtractionSnapshot` restore semantics in `_recover_ocr_escalation` (the original spec's case 3 — "defect changes GARBLING→NODE_COUNT_LOW after failed OCR escalation" — is likely unreachable as stated because snapshot restore reverts to the pre-retry defect; confirm via code reading before asserting) (assertion_type: contract)
- `tests/test_zone2_persist_fail_route.py` — `Route.PERSIST_FAIL` explicitly persists tree with FAIL verdict via `_persist_tree_result`, does not raise `LowQualityTreeError`; regression test for the RFC-029 D0/D1/D2/D8 unwired-defect bug class (assertion_type: regression)
- `tests/test_zone2_recovery_state_coherence.py` — after each recovery method mutates state, `first_defect`/`route` remain coherent with `gate_result`; `route_overridden` correctly gates `_finalize_routing` recomputation (assertion_type: contract)
- New: `tests/test_zone2_flat_garble_unrecovered_guard.py` — a doc with `flat_garble_unrecovered=True` and `route` in `{TREE, PERSIST_FAIL}` is rejected, not persisted (regression test for the dropped-guard gap found in validation) (assertion_type: regression)

**Corpus validation:** affected documents marsoom-13, Penal_Code, federal_decree_law_no_33, marsoom-33, arabicSLA, SLA, MOU, warid-597, cabinet_resolution_no_96, qarar-106, GHV-TKV-Tarif.pdf, Haftpflicht; expected verdict direction: stable; spot-check count: 6.

**Estimated complexity:** medium.

## Validation Results

**Overall quality:** needs_work
**Approved:** false

The plan as originally drafted was **not approved for direct implementation**. Nine issues were found during validation; the fix specs above have corrected code targets, but the underlying issues are recorded here in full so the corrections are auditable against the original claims.

### Zone 2 — blocker

Code target for the match/case dispatch (originally "lines 2110-2160") was built on a factually wrong reading of the code: it claimed `_recover_flat_prefer` and `_recover_landscape_reroute` set `state.ok = True` with `Route.FLAT`, specifying `case (True, Route.FLAT) -> _persist_flat_result` for them. Both methods actually set `state.ok = False` (client.py:1683-1684 and :1713-1714). Implementing case 5 as originally written would change semantics, directly violating the spec's own constraint that outcomes must remain identical to current behavior. **Fix applied above:** corrected state model — flat-prefer/landscape-reroute produce `(ok=False, route=FLAT)`, already covered by case 4; case `(True, Route.FLAT)` is left as an open item requiring proof of reachability before coding.

### Zone 2 — major (3 issues)

1. The proposed match/case enumeration was not exhaustive over reachable `(state.ok, state.route)` combinations: `(False, Route.TREE)` was unhandled. Today that combination reaches the "persist low-quality tree with FAIL verdict" path by fallthrough (~2143-2148). With "no bare `case _`" plus `assert_never` as originally specified, this state would crash at runtime instead of persisting with FAIL. **Fix applied:** case 3 added explicitly to the corrected case table.
2. The dispatch redesign dropped `state.flat_garble_unrecovered` from the routing decision. The current reject condition is `state.route in (Route.REJECT, Route.FLAT) or state.flat_garble_unrecovered` (~2126-2129); a match purely on `(state.ok, state.route)` loses this orthogonal reject trigger, so a doc with `flat_garble_unrecovered=True` and `route=TREE/PERSIST_FAIL` would be persisted instead of rejected. **Fix applied:** explicit pre-match guard clause added (case 1) plus a new regression test.
3. `_finalize_routing`'s override-detection mechanism ("if `state.route == Route.FLAT`, skip recomputation") is unsound: it cannot distinguish a recovery override from `decide_route()`'s own first-pass result, which can legitimately return `Route.FLAT` (e.g. `NODE_COUNT_LOW` with flat-doc-routing on). The spec provided no mechanism able to actually detect an override. **Fix applied:** explicit `state.route_overridden: bool` flag added to `ExtractionState`, set at all 5 override sites, gating recomputation instead of inferring from route value.

### Zone 2 — minor

Wiring check for `ROUTE_VALUES` was self-referential and vacuous (defined in helpers.py, checked as imported by helpers.py, with no production consumer — only a test file, which wiring-check rules exclude). **Fix applied:** dropped `ROUTE_VALUES`; tests compute `frozenset(Route)` directly from the enum; `helpers.py` target replaced with the `route_overridden` field, which does have a production consumer (`_finalize_routing` and the 5 override sites).

`tests/test_zone2_finalize_routing.py` case 3 as originally specified ("OCR escalation fails but gate_result changes defect GARBLING→NODE_COUNT_LOW → route recomputed to FLAT") is likely unreachable: `_finalize_routing`'s design skips recomputation whenever route is already FLAT, and `_recover_ocr_escalation`'s failure path restores from `ExtractionSnapshot` (pre_retry at ~1287), which restores the pre-retry defect. **Fix applied:** test requirement now instructs deriving cases from actual snapshot-restore semantics before locking in assertions, rather than asserting the original (possibly unreachable) transition.

### Zone 1 — major

The entire original Zone 1 spec (thread `expected_script` through the chain) is **already implemented, uncommitted, in the working tree**: `expected_script` is passed at both `conv_fn` call sites (client.py ~1072, ~1088), `_pdf_to_markdown_no_pics` already takes `**kwargs` (converters.py ~3496-3497), the chain annotation is already `Callable[...]` (converters.py ~3509), the remote-Docling known-gap comment exists (client.py ~1035), and `tests/test_zone1_chain_expected_script.py` plus `test_zone1_garble_wiring.py` already exist (the former untracked per `git status`). Executing the spec as fresh wave-1 implementation would at best be a no-op and at worst double-apply edits or clobber the uncommitted implementation. **Fix applied:** Wave 1 rationale and scope rewritten above from "implement" to "review, verify wiring, run tests, and commit the existing working-tree diff."

### Zone 1 — minor (3 issues)

1. Committed-code line numbers in the original spec were off: the docling full-page-OCR `conv_fn` invocation is at HEAD line ~1059 (call spans ~1057-1064), not "~1068"/target range 1065-1073 as originally stated — those numbers actually match the post-fix working tree (1068/1086). The general-branch line 1075 was correct at HEAD. **Fix applied:** code targets above are labeled explicitly as working-tree line numbers, not committed-code line numbers.
2. Wiring check `_pdf_to_markdown_no_pics(**kwargs) must_be_imported_by src/pageindex_mcp/converters.py` was self-referential (function defined in converters.py, checked as imported by converters.py) and passes trivially without proving the kwarg actually flows. **Fix applied:** replaced with a contract-test-based check (kwarg-acceptance verified via unit call in `tests/test_zone1_chain_expected_script.py`) rather than a vacuous import check.
3. Wave-1 rationale in the original plan was stale and self-contradictory: it claimed Zone 1 "rewrites garble functions at helpers.py lines 1423-1545, 1861, 3228-3245" and listed `helpers.py` as a shared file, but the Zone 1 spec body itself says that consolidation already landed in commits `33cc1f5`/`113c33a` and contains no `helpers.py` code target. Similarly the wave-2 rationale claimed Zone 2's recovery methods "will import and call `check_garble()`," but Zone 2's code targets contain no garble work — those methods already exist and already call `check_garble()`. **Fix applied:** both wave rationales rewritten above against the current residual specs: Wave 1 = `client.py`/`converters.py` threading (review/commit) only; Wave 2 depends on Wave 1 solely through the shared `client.py` file, not through an API dependency.

---

## Wave 4 — Zone 3: Verdict Persistence Split-Brain

**No file overlap with Zones 1/2/4/5.** Key files: `promotion_sweep.py`, `src/pageindex_mcp/worker.py`, `preprocess_client.py`, `src/pageindex_mcp/storage.py`.

**Mechanism:** The two offline verdict recomputers (`promotion_sweep.py` and `preprocess_client.py recompute_verdicts`) produce divergent verdicts for the same document. `promotion_sweep` reconstructs a `TreeGateResult` from stored `verdict_reason` via `_defect_from_reason_str` (line 96), recovering only a single defect and returning `TreeDefect.OK` for unrecognized strings — silently promoting gate-rejected docs. `recompute_verdicts` re-runs `validate_tree` on stored structure with current gate logic (line 338). Additionally, `_upsert_registry_row` (worker.py:674) re-reads the just-persisted MinIO artifact across a process boundary with no consistency guarantee, creating a write-visibility race where registry rows can miss verdict fields.

**Strategy:** Replace `_defect_from_reason_str` in `promotion_sweep.py` with `validate_tree` call on stored structure (matching `recompute_verdicts`). Both offline paths then use identical gate logic. For the worker registry write race, pass verdict fields directly from the job result dict instead of re-reading from MinIO.

**Code targets:**

| File | Lines | What | How | Constraint |
| --- | --- | --- | --- | --- |
| `promotion_sweep.py` | 91-101 | Replace `_defect_from_reason_str` with `validate_tree` | `vt_result = validate_tree(structure)` then pass to `classify_verdict`. Remove `_defect_from_reason_str` import, remove stored_reason/sidecar read (lines 78-89) | Keep existing `write_verdict` call unchanged. Confirm sidecar read has no other consumers before removing |
| `promotion_sweep.py` | 18-25 | Update imports | Replace `_defect_from_reason_str` with `validate_tree` in helpers imports | Confirm all removed-import references are gone |
| `src/pageindex_mcp/worker.py` | 674-701 | Pass verdict fields from job result, not MinIO re-read | Add `verdict_fields: dict | None = None` to `_upsert_registry_row`; merge into registry fields | Must remain best-effort. Default None preserves backward compat |
| `src/pageindex_mcp/worker.py` | 600-670 | Thread verdict fields to `_upsert_registry_row` call | Extract verdict/verdict_reason/pipeline_version from job result dict, pass as `verdict_fields` kwarg | Check job result dict shape — verify which fields the child subprocess returns |

**Test requirements:**
- `tests/test_zone3_verdict_split_brain.py` (contract) — `promotion_sweep` calls `validate_tree` not `_defect_from_reason_str`
- `tests/test_zone3_verdict_split_brain.py` (regression) — both recomputers produce identical verdicts for same structure+content_class
- `tests/test_zone3_verdict_split_brain.py` (contract) — `_upsert_registry_row` with `verdict_fields` merges them; without (None) behavior unchanged
- `tests/test_zone3_verdict_split_brain.py` (wiring) — AST-parse: `_defect_from_reason_str` NOT imported; `validate_tree` IS imported in `promotion_sweep.py`

**Wiring checks:** `validate_tree` called from `promotion_sweep.py`; `_upsert_registry_row` called from `src/pageindex_mcp/worker.py`.

**Estimated complexity:** medium.

---

## Wave 5 — Zone 6: Conversion Pipeline Stage Coupling (body_for_containment decoupling)

**No file overlap with Zones 1/2/3/4.** Key file: `src/pageindex_mcp/converters.py` (different section from Zone 1's chain threading).

**Mechanism:** Stage coupling in `pdf_to_markdown_docling` (342 lines, lines 3148-3493) means `body_for_containment` exists solely to undo a prior stage's side effect (`_document_level_text_fallback` inflating md). The pre/post fallback stage split (lines 3429-3449) is fragile: inserting a new stage in the wrong list silently breaks the containment snapshot. The two-candidate source selection (lines 3387-3413) runs heading recovery independently on both via `_candidate_from_document`, then selects winner based on structural depth — but re-calls `_has_structural_depth` instead of reading it from the candidate.

**Strategy:** Extract pre/post fallback stage split into `_run_fallback_pipeline` helper returning `(md, body_for_containment, stages)` — making the snapshot an explicit output. Add `has_depth` to `Candidate` namedtuple. Consolidate repeated `len(_HEADING_RE.findall(...))` into `_heading_count` helper.

**Code targets:**

| File | Lines | What | How | Constraint |
| --- | --- | --- | --- | --- |
| `src/pageindex_mcp/converters.py` | 3426-3449 | Extract `_run_fallback_pipeline` helper | Returns `(final_md, body_for_containment, combined_stages)`. Runs pre-stages, snapshots, runs post-stages | Preserve exact stage ordering. body_for_containment must reach `_recover_picture_results` |
| `src/pageindex_mcp/converters.py` | 3385-3413 | Add `has_depth` to Candidate, use in selection | `_candidate_from_document` computes `_has_structural_depth` on result and stores it. Selection reads `candidate.has_depth` | Candidate is already a namedtuple — add field. No change to selection logic semantics |
| `src/pageindex_mcp/converters.py` | 3350-3374 | `_heading_count(md)` helper | Replace 12+ instances of `len(_HEADING_RE.findall(...))` | Pure refactor — `_HEADING_RE` pattern unchanged |

**Test requirements:**
- `tests/test_zone6_fallback_pipeline.py` (contract) — `_run_fallback_pipeline` returns `body_for_containment` as md before `_document_level_text_fallback`
- `tests/test_zone6_fallback_pipeline.py` (contract) — `Candidate.has_depth` matches `_has_structural_depth(candidate.md)`
- `tests/test_zone6_fallback_pipeline.py` (regression) — `pdf_to_markdown_docling` produces identical output before/after refactor

**Wiring checks:** `_run_fallback_pipeline` called from `src/pageindex_mcp/converters.py` (internal).

**Estimated complexity:** medium.
