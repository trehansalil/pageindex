# Remediation Plan — 2026-08-18

**Audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-18_POST-FIX-6.md
**Zones:** 5 of 8 (top by priority)
**Waves:** 3
**Validation status:** APPROVED — all 4 blockers resolved (2026-08-18 patch). Wave 1: Zone 1 then Zone 4 (serialized); Wave 2: Zone 2 ∥ Zone 7 (shared files declared); Wave 3: Zone 3 (extended recovery_tag coverage).

---

## Priority Scores

| Zone | Score | Severity | Bug Count | Proposal Status | In This Plan? |
|---|---|---|---|---|---|
| Zone 1: Garble Detection Surface Sprawl | 50.6 | critical | 11 | not_implemented (+15% regression-history boost) | Yes — Wave 1 |
| Zone 2: Dual Verdict Authority (validate_tree vs classify_verdict) | 46.0 | critical | 10 | not_implemented (+15% boost) | Yes — Wave 2 |
| Zone 3: Recovery Pipeline Implicit Ordering and State Mutation | 41.4 | critical | 9 | not_implemented (+15% boost) | Yes — Wave 3 |
| Zone 4: Picture/OCR Recovery Dual-Path Conflation | 24.0 | high | 8 | not_implemented | Yes — Wave 1 |
| Zone 7: Silent Fallback Chains Masking Compliance and Quality Failures | 20.7 | high | 5 | no_proposal (+15% boost, x1.2 no-proposal multiplier) | Yes — Wave 2 |
| Zone 6: Splitter Pattern Fragility and Giant Tail-Blob Recurrence | 18.0 | high | 5 | no_proposal | **No** — audit recommends a design spike before any code-fix cycle; not actionable as a mechanical fix |
| Zone 5: Cross-Process Verdict/Registry Write Races | 15.0 | high | 5 | not_implemented | **No** — excluded to keep this plan to the top-5 by score; good standalone candidate for a future wave (low collision risk with this cluster) |
| Zone 8: Duplicated Threshold/Logic Definitions Across Files | 9.6 | medium | 4 | no_proposal | **No** — lowest priority, mechanical follow-up, independently fixable any time |

Scoring formula: `severity_weight(critical=4, high=3, medium=2) x bug_count x proposal_multiplier(not_implemented=1.0, no_proposal=1.2)`, with a further +15% applied where the audit documents a stalled fix/regress history.

---

## Wave Sequence

### Wave 1a — Zone 1 (solo, lands first)
**Rationale:** Zone 1 deletes the `GarbleContext` enum and replaces it with `GarbleProfile` (frozen dataclass + two constant profiles). All downstream zones that reference `GarbleContext` must target the post-fix API. Zone 1 runs first and alone.

### Wave 1b — Zone 4 (solo, after Zone 1 lands)
**Rationale:** Zone 4 targets `converters.py:2299-2316` which overlaps Zone 1's target on the same `GarbleContext.REGION` block. Zone 4's spec has been **rewritten** to target Zone 1's post-fix API (`profile=BULK_PROFILE` instead of `GarbleContext.REGION`). Runs after Zone 1 lands to avoid merge conflicts and dangling-API bugs.

**RESOLVED (2026-08-18):** Original blocker — Zone 1 and Zone 4 both edited `converters.py:2299-2316` and Zone 4 referenced `GarbleContext.REGION` which Zone 1 deletes. Fixed by serializing Wave 1 into 1a→1b and rewriting Zone 4's targets against post-fix API.

### Wave 2 — Zone 2 + Zone 7
**Rationale (as proposed):** Zone 2 (`helpers.py`, `client.py`) depends on Zone 1 landing first (both rewrite `helpers.py` verdict/garble code) and must land before Zone 3 (Zone 3's recovery pipeline feeds `_persist_flat_result`/`_persist_tree_result`). Zone 7 (`converters.py`, `config.py`, `worker.py`) depends on Zone 4 landing first (shared `converters.py`). Zone 2 and Zone 7 were claimed to touch disjoint files so can parallelize.

**Shared files as declared:** none.

**Shared files as verified (MAJOR — see Validation Results):** the "disjoint files" claim is false. Zone 7 touches `client.py` (lines 83-95, 1169-1177) and Zone 2 touches `client.py` (lines 74, 1897-1904, 2006-2014); Zone 2 also touches `converters.py:947-948` while Zone 7 touches `converters.py:1174-1176` and `metrics.py` (not inventoried anywhere in the wave). Line ranges themselves don't overlap, so this is a coordination hazard rather than a guaranteed conflict — but the `shared_files: []` declaration is factually wrong and must be corrected before parallel dispatch. Recommended: declare `client.py` and `converters.py` as shared with non-overlapping line ownership, or simply serialize the small, observability-only Zone 7 after Zone 2.

**RESOLVED (2026-08-18):** `_RECOVERY_REGISTRY` dropped from Zone 2 entirely. Recovery-coverage assertion lives solely in Zone 3's `GateSpec.recovery_tag` mechanism. RAISE-policy gates (NODE_COUNT_LOW, DEPTH_LOW, REORDERED) are explicitly excluded from requiring `recovery_tag` — they use the existing `RAISE` policy to surface errors, not trigger recovery. This matches the current production behavior where RAISE gates halt processing rather than attempting recovery.

### Wave 3 — Zone 3 (alone)
**Rationale:** Zone 3 (`client.py`, `helpers.py`) is the cross-zone integration point — `_recover_ocr_escalation` transitively reaches Zone 1's garble internals, Zone 2's `validate_tree`/`prepare_tree`, and uses `ExtractionSnapshot` (`helpers.py`). It shares `helpers.py` with Zone 1 (Wave 1) and both `helpers.py` and `client.py` with Zone 2 (Wave 2), so it must run last: it consumes the garble API stabilized by Zone 1 and the verdict contract stabilized by Zone 2, and `_finalize_routing`'s call into `decide_route` must align with the Wave-2 verdict authority. Running it alone avoids file collisions with prior waves.

**RESOLVED (2026-08-18):** Extended `recovery_tag` coverage to wire all gate-driven recoveries:
- `GARBLING`/`NODE_GARBLING` → `recovery_tag='ocr_escalation'` (unchanged)
- `RTL_REVERSAL` → `recovery_tag='rtl_repair,rtl_flat_compare'` (multi-tag: both fire sequentially)
- `IMAGE_DOMINANT` → `recovery_tag='image_dominant_ocr'` (new)
- `VLM_HINT` → `recovery_tag='vlm_fallback'` (new — fires when VLM hint gate flags VLM-amenable content)
- `flat_prefer` + `landscape_reroute` remain post-loop quality checks (not gate-driven, always run)
- RAISE-policy gates (`NODE_COUNT_LOW`, `DEPTH_LOW`, `REORDERED`) explicitly excluded — they halt processing, not trigger recovery
- Test requirement updated: "NODE_COUNT_LOW triggers RAISE (no recovery)" replaces contradictory "NODE_COUNT_LOW triggers image_dominant_ocr"

---

## Fix Specs

### Zone: Zone 1: Garble Detection Surface Sprawl (wave 1, priority 1)

**Mechanism to eliminate:** One shared garble engine (`garble_prongs`, `helpers.py:1251`) with context-dependent behavior dispatched through three layers of indirection: an 8-member `GarbleContext` StrEnum (`helpers.py:1360-1372`) selecting call-site identity, `_garble_context_short_circuit` (`helpers.py:1386-1408`) returning early True/False for `FLAT_MARKDOWN` only, and `_garble_context_blob_kind` (`helpers.py:1411-1420`) selecting normalization strategy per context. Only 2 distinct behaviors exist across 8 context values, but the 8-way dispatch creates a false sense of independent configuration. `expected_script` self-inference at `garble_prongs:1328` also silently enables the `latin_gibberish` prong when callers pass `None`, making it impossible to explicitly skip the prong. Each new prong or context-specific rule closes one blind spot while creating false positives in another context through the shared engine.

**Strategy:** Deletion-first consolidation. Delete the 3-layer indirection (`GarbleContext` enum + `_garble_context_short_circuit` + `_garble_context_blob_kind` + `_is_garbled_blob` wrapper). Replace with a frozen `GarbleProfile` dataclass (`normalize_markdown`, `short_circuit_prior_garble` booleans) and exactly two module-level constants, `BULK_PROFILE` and `FLAT_MARKDOWN_PROFILE`. Remove `expected_script` self-inference from `garble_prongs` so callers must explicitly call `infer_script` when inference is desired. `check_garble` takes `profile=` instead of `context=`. Net deletion ~29 lines. All 16 production call sites migrate mechanically (7 of 8 `GarbleContext` values map to `BULK_PROFILE`; only `FLAT_MARKDOWN` maps to `FLAT_MARKDOWN_PROFILE`).

**Estimated complexity:** large

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers.py` | 1343-1377 | Add `GarbleProfile` dataclass + `BULK_PROFILE`/`FLAT_MARKDOWN_PROFILE`; delete `GarbleContext` enum + 2 module vars | `@dataclass(frozen=True) GarbleProfile(normalize_markdown: bool=False, short_circuit_prior_garble: bool=False)`; `BULK_PROFILE=GarbleProfile()`; `FLAT_MARKDOWN_PROFILE=GarbleProfile(True, True)`. Move `_GARBLE_SHORT_TEXT_DEFAULT`/`_GARBLE_FLAT_MARKDOWN_NORMALIZE` env reads to module level but reference only inside `check_garble` | Must preserve monkeypatchability of both env vars from `test_rfc025_d2.py:29` and `test_zone1_garble_consolidation.py:235`; read at call time, not frozen into the profile |
| `helpers.py` | 1345-1357 | Delete `_is_garbled_blob` wrapper | Remove function; inline `garble_prongs` + `normalize_for_garble` into `check_garble` | Update `check_garble`; tests importing `_is_garbled_blob` (3 files) must update |
| `helpers.py` | 1386-1420 | Delete `_garble_context_short_circuit` and `_garble_context_blob_kind` | Inline logic into `check_garble` via `profile.short_circuit_prior_garble` / `profile.normalize_markdown` | No observable behavior change for any (profile, text_length, original_defect, env_vars) combination |
| `helpers.py` | 1423-1458 | Rewrite `check_garble` signature to `profile: GarbleProfile` | New sig: `check_garble(text, *, expected_script, profile, original_defect=None)`; inline short-circuit + blob_kind + `garble_prongs` call directly (no wrapper) | `context=` removed with no back-compat shim; `expected_script` stays keyword-only |
| `helpers.py` | 1251-1342 | Purify `garble_prongs`: remove `blob_kind` param and internal normalization; remove self-inference at line 1328 | Rename `blob`→`norm_blob` (pre-normalized input expected); `_effective_script = expected_script` (no `_infer_script` fallback) | Stays a pure function returning `frozenset[str]`; all 12 prongs unchanged; empty-blob guard stays |
| `helpers.py` | 370-408 | `TreeSignals.from_tree`: `GarbleContext`→`BULK_PROFILE`, explicit script inference | `eff_script = expected_script or _infer_script(flat_text)` once, pass to both `check_garble` and `_garble_ratio` | No behavior change when `expected_script` non-None |
| `helpers.py` | 1579-1627 | `_garble_check_nodes`: `context=GarbleContext.NODE`→`profile=BULK_PROFILE` (2 sites) | Mechanical replacement | Identical behavior (NODE had same normalization/no-short-circuit as BULK) |
| `helpers.py` | 2055-2073 | `_garble_ratio`: `context=GarbleContext.TREE_BULK`→`profile=BULK_PROFILE` (2 sites) | Mechanical replacement | Identical behavior |
| `helpers.py` | 2316-2319 | `classify_verdict`: `context=GarbleContext.IMAGE_ENRICHMENT`→`profile=BULK_PROFILE`, add explicit inference | `expected_script=expected_script or _infer_script(_promoted_text)` | Must add inference to prevent silent `latin_gibberish` regression |
| `client.py` | 57-74 | Import block: `GarbleContext`→`GarbleProfile`, `BULK_PROFILE`, `FLAT_MARKDOWN_PROFILE` | Remove `GarbleContext` from import list; add the 3 new symbols | No unused imports; check `infer_script` isn't double-imported |
| `client.py` | 456,1046,1412,1417,1425,1823,1849 | 7 `check_garble` call sites: `context=`→`profile=` | `FLAT_MARKDOWN`→`FLAT_MARKDOWN_PROFILE` (456,1046,1823,1849); `RETRY_COMPARISON`→`BULK_PROFILE` (1412,1417,1425) | Preserve `original_defect=` at line 1823 |
| `converters.py` | 1777-1780,1901-1903,2306-2311 | 3 lazy imports + 3 call sites: `GarbleContext`→`BULK_PROFILE` | `PAGE_TEXT_LAYER`, `DOCUMENT_FALLBACK`, `REGION` all map to `BULK_PROFILE` | Lazy imports stay lazy (circular-import avoidance); `infer_script` import preserved |

> ⚠️ Line-drift note from validation: actual `check_garble` call sites in `client.py` verified at 457/1044/1410/1415/1423/1821/1847 (1-2 lines off from spec). GATES entries: GARBLING is at 1845, NODE_GARBLING at 1848, RTL_REVERSAL at 1850 — the spec's 1847/1849 anchors are wrong (1847 is actually `DEPTH_LOW`, 1849 is `REORDERED`). **Key edits by `TreeDefect` name, not by line number, to avoid mislabeling.**

#### Wiring checks

| Symbol | Must be imported by | Check type |
|---|---|---|
| `GarbleProfile` | `client.py` | import |
| `BULK_PROFILE` | `client.py`, `converters.py` | import |
| `FLAT_MARKDOWN_PROFILE` | `client.py` | import |
| `check_garble` | `client.py`, `converters.py` | call |

> Removed from the original spec: the `GarbleProfile.evaluate` wiring check (call type, empty `must_be_imported_by`) is **dropped** — no such method is defined anywhere in the spec; it was a phantom/vacuous check.

#### Test requirements

- `tests/test_zone1_garble_profile.py` — `GarbleProfile` contract exhaustiveness: frozen dataclass, exactly 2 boolean fields, exactly 2 module-level profile constants with correct values, `FrozenInstanceError` on mutation, `GarbleContext` no longer importable.
- Same file — `check_garble` contract: `profile=` and `expected_script=` both keyword-only-required, `TypeError` otherwise.
- Same file — behavioral equivalence regression across 8 text samples (clean Latin/Arabic, PUA garble, null-byte garble, digit-ratio garble, token-repetition garble, Latin-gibberish-in-Arabic, sparse mojibake): `BULK_PROFILE` matches old TREE_BULK/NODE/PAGE_TEXT_LAYER/DOCUMENT_FALLBACK/REGION/RETRY_COMPARISON/IMAGE_ENRICHMENT; `FLAT_MARKDOWN_PROFILE` matches old FLAT_MARKDOWN.
- Same file — self-inference removal regression: `garble_prongs(expected_script=None)` on Arabic text does not fire `latin_gibberish`; `TreeSignals.from_tree` with `expected_script=None` still detects it via explicit `_infer_script`; `check_garble(BULK_PROFILE, expected_script=None)` on Latin gibberish does not fire.
- Same file — `FLAT_MARKDOWN_PROFILE` short-circuit behavior (4 cases incl. env-var monkeypatch disabling it).
- Same file — wiring verification per table above.
- Same file — `garble_prongs` purification contract (no `blob_kind` param, pre-normalized input, no self-inference, still returns `frozenset[str]` of 12 prongs).
- Same file — integration: `validate_tree` → `TreeSignals.from_tree` → `check_garble` → `garble_prongs` chain on a known-garbled tree (PUA chars) and a clean German tree; `_gate_garbling`/`_gate_node_garbling` both work through the profile-based path.

#### Corpus validation

- Affected documents: `marsoom-13`, `siyasat-hawkama`, `human-rights-ar`, `reitlehrer`, `penal-code`, `cabinet-resolution`, `ward-597`
- Expected verdict direction: stable
- Spot-check count: 5

---

### Zone: Zone 2: Dual Verdict Authority (validate_tree vs classify_verdict) (wave 2, priority 2)

**Depends on:** Zone 1 (garble profile API must be stable first — `classify_verdict` calls `check_garble`).

**Mechanism to eliminate:** Two independent verdict engines — `validate_tree` (`helpers.py:1888`, `TreeGateResult` via 10-gate `GATE_TABLE`) and `classify_verdict` (`helpers.py:2199`, a 195-line grouped-rule engine with 7+ independent promotion branches) — that can disagree on document quality. `classify_verdict` applies independent checks (max-leaf-ratio hard-fail, content-class promotions, image-enrichment rescue, depth-adequacy clamp) that can override or mask `validate_tree`'s decision. For flat-path documents, `validate_tree` is bypassed entirely (`_persist_flat_result` passes `validate_result=None`), silently skipping all 10 quality gates. `classify_verdict` is called twice with potentially different inputs — once in `_candidate_from_document` (source selection, lightweight structure, `validate_result=None`) and once in `client.py` on the final post-`prepare_tree` structure — which can produce diverging verdicts. `_compute_verdict_band` (`helpers.py:2156`) is a third layer of indirection on the hard-fail decision. New `validate_tree` gates shipped without recovery wiring cause cascading PASS-to-ERROR regressions (RFC-029 D1: 4 new failure reasons, no recovery path).

**Strategy:** Consolidate `validate_tree` and `classify_verdict` into a single `compute_verdict` function: phase 1 runs `GATE_TABLE` evaluation, phase 2 applies content-class promotions/caps, returning a `VerdictResult` dataclass. Eliminate the `validate_result=None` silent-skip path via an explicit `FLAT_GATE_SUBSET`. Add `source_selection` mode for the early `converters.py` call to skip persistence-only caps (`_clamp_pass`). Fold `_compute_verdict_band` inline. Add a module-level exhaustive-coverage assertion for RETRY_OCR/RETRY_RTL/RAISE-policy gates. `validate_tree` and `classify_verdict` survive as thin backward-compat wrappers around `compute_verdict`. Migration is 5-step and corpus-diffable at each step.

> **RESOLVED (2026-08-18):** `_RECOVERY_REGISTRY` dropped from Zone 2. Recovery-coverage assertion lives solely in Zone 3's `GateSpec.recovery_tag`. RAISE-policy gates excluded from requiring recovery wiring (they halt, not recover). The `helpers.py:1862-1865` code_target for `_RECOVERY_REGISTRY` assertion is deleted (see table row above marked ~~strikethrough~~).

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers.py` | 106-107 | Add `VerdictResult` frozen dataclass after `TreeGateResult` | Fields: `verdict, reason, defect, signals, all_defects`; `__iter__` yields `(verdict, reason)` for back-compat tuple-unpacking | Must not break 40+ existing `verdict, reason = classify_verdict(...)` call sites |
| `helpers.py` | 1860-1868 | Add `FLAT_GATE_SUBSET` constant | Derive from `GATES`: `[(g.gate_fn, g.defect) for g in GATES if g.gate_fn and g.defect in _FLAT_APPLICABLE_DEFECTS]` where `_FLAT_APPLICABLE_DEFECTS = {GARBLING, NODE_GARBLING, REORDERED}` | Must derive from `GATES` (auto-syncs with new gates); must NOT include `NODE_COUNT_LOW`/`DEPTH_LOW` |
| `helpers.py` | 1862-1865 | ~~Add `_RECOVERY_REGISTRY` exhaustive-coverage assertion~~ **REMOVED — see blocker note above; superseded by Zone 3's `GateSpec.recovery_tag`** | — | — |
| `helpers.py` | 2156-2174 | Delete `_compute_verdict_band`, fold into `compute_verdict` phase 1 | Move 19-line hard-fail body inline into GROUP 1 block | Hard-fail semantics and `_GATE_PRIORITY` tiebreak order preserved exactly |
| `helpers.py` | 2199-2393 | Rename `classify_verdict`→`compute_verdict`; return `VerdictResult`; add `source_selection`/`flat` params; keep `classify_verdict` as thin wrapper | Phase 1: when `validate_result is None and flat=True`, run `FLAT_GATE_SUBSET` instead of skipping gates. Phase 2: unchanged grouped-rule logic; `source_selection=True` skips `_clamp_pass` | Tree-path output must be byte-identical to current `classify_verdict` for every input combination; `flat`/`source_selection` are orthogonal flags |
| `client.py` | 1897-1904 | `_persist_flat_result`: call `compute_verdict(..., flat=True)` instead of `classify_verdict(..., validate_result=None)` | `_vr = compute_verdict(...); f_verdict, f_verdict_reason = _vr.verdict, _vr.reason` | Sidecar string format unchanged; verify no unexpected verdict flips on corpus before enabling |
| `client.py` | 2006-2014 | `_persist_tree_result`: `classify_verdict`→`compute_verdict` | Same unpack pattern | Must be identical to current tree-path output |
| `converters.py` | 947-948 | `_candidate_from_document`: `classify_verdict(..., None)`→`compute_verdict(..., source_selection=True)` | `_vr = compute_verdict(...); verdict = _vr.verdict` (plain string on `Candidate.verdict`) | Preserve try/except + structural-depth-proxy fallback |

#### Wiring checks

| Symbol | Must be imported by / consumed where | Check type |
|---|---|---|
| `VerdictResult` | `client.py` | import |
| `compute_verdict` | `client.py`, `converters.py` | import + call |
| `FLAT_GATE_SUBSET` | consumed **inside `compute_verdict`'s `flat=True` branch body** (not merely defined in `helpers.py`) | dispatch |

> Corrections from validation: (1) `VerdictResult` is **not** required in `converters.py` — `_candidate_from_document` only needs `compute_verdict`'s `.verdict` attribute, no type annotation forces the import; dropped from that file's wiring check to avoid a forced-unused-import. (2) The `FLAT_GATE_SUBSET` check must verify the symbol is referenced *inside* `compute_verdict`'s flat branch, not merely that `helpers.py` contains its definition — a same-file-only check would trivially pass even if the constant were built and never consulted.

#### Test requirements

- `tests/test_zone2_compute_verdict.py` — `VerdictResult` correctness across all 3 modes (tree-path/flat/source_selection) + `__iter__` back-compat.
- `tests/test_zone2_flat_gate_subset.py` — flat docs with garbling/reordering now caught (previously silently skipped); `FLAT_GATE_SUBSET` excludes `NODE_COUNT_LOW`/`DEPTH_LOW`; clean flat doc unchanged.
- `tests/test_zone2_gate_recovery_exhaustiveness.py` — **retarget to Zone 3's `GateSpec.recovery_tag` mechanism** once the blocker above is resolved; do not duplicate a second registry.
- `tests/test_zone2_verdict_result_wiring.py` — per wiring table above.
- `tests/test_zone2_verdict_regression.py` — all 5 hard-fail defects, all promotion paths, all caps, MARGINAL fallback reasons, parametrized over well-formed/single-leaf/shallow trees and `image_standalone` content-class.
- **Added per validation (previously missing coverage):** a regression test importing `classify_verdict` exactly as `preprocess_client.py`/`promotion_sweep.py` do (external, outside `src/pageindex_mcp/`) confirming tuple-unpacking still works end to end post-refactor — these two scripts are named as explicit backward-compat beneficiaries but had zero test coverage in the original spec.

#### Corpus validation

- Affected documents: flat-path docs (`flat_prose`/`flat_mixed`) with previously-undetected garbling/reordering; docs processed through `_candidate_from_document` where `_clamp_pass` was incorrectly applied at screening time; any doc where the two `classify_verdict` call sites diverged
- Expected verdict direction: stable
- Spot-check count: 8

---

### Zone: Zone 3: Recovery Pipeline Implicit Ordering and State Mutation (wave 3, priority 3)

**Depends on:** Zone 1, Zone 2 (garble API and verdict authority must be stable — `_recover_ocr_escalation` reaches into both).

**Mechanism to eliminate:** Implicit ordering of 7 recovery methods via sequential call sequence (`client.py:2197-2210`) with hidden dependencies on shared mutable `ExtractionState` (~20 fields, `helpers.py:176-207`). Each recovery reads `state.first_defect` to decide whether to fire, but prior recoveries may change it as a side effect of `_reconvert_and_revalidate`. `ExtractionSnapshot.restore()` (`helpers.py:164-172`) uses 8-element positional tuple destructuring with `gate_result` duplicated at positions 3 and 4. `route_overridden` and `original_gate_result` are workaround fields patching over the implicit-ordering problem. New gates shipped without recovery wiring cause PASS-to-ERROR regressions (RFC-029 D1).

**Strategy:** Deletion-first (validated pattern, zero regressions across 4 prior zone-fix cycles). Add `recovery_tag` to `GateSpec`; introduce `RecoveryOutcome` frozen dataclass (all-Optional, `apply(state)` method) replacing the positional-tuple snapshot; replace the 7 sequential calls with a declarative gate-driven loop dispatching via `RECOVERY_DISPATCH`; delete `ExtractionSnapshot` (-66 lines); remove `route_overridden`/`original_gate_result` from `ExtractionState`; delete `_finalize_routing` (inlined into the loop).

> **RESOLVED (2026-08-18):** `recovery_tag` coverage extended to all gate-driven recoveries:
> - `GARBLING`/`NODE_GARBLING` → `'ocr_escalation'` (unchanged)
> - `RTL_REVERSAL` → `'rtl_repair,rtl_flat_compare'` (multi-tag: both fire sequentially)
> - `IMAGE_DOMINANT` → `'image_dominant_ocr'` (new)
> - `VLM_HINT` → `'vlm_fallback'` (new)
> - `flat_prefer` + `landscape_reroute` remain post-loop quality checks (always run, not gate-driven)
> - RAISE-policy gates (`NODE_COUNT_LOW`, `DEPTH_LOW`, `REORDERED`) excluded — they halt, not recover
> - Test requirement corrected: "NODE_COUNT_LOW triggers RAISE" (not recovery)

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers.py` | 107-173 | Delete `ExtractionSnapshot`; add `RecoveryOutcome` frozen dataclass | All-Optional fields (`result, ok, reason, gate_result, md_content, pic_results, used_converter, total_chars, route`, plus `rtl_decision` with sentinel default) + `apply(state)` | `_recover_ocr_escalation` restore path must produce identical post-state; sentinel distinguishes "clear" (`None`) from "no change" (`SENTINEL`) for `rtl_decision` |
| `helpers.py` | 176-207 | Remove `original_gate_result` and `route_overridden` from `ExtractionState` | Delete both fields | `_persist_tree_result` must switch to reading `state.gate_result` (semantically identical — always set equal after each revalidation); 5 test files constructing `ExtractionState` must drop these fields |
| `helpers.py` | 220-233 | Add `recovery_tag: str \| None = None` to `GateSpec` | — | Backward-compatible default for all 12 existing `GATES` entries |
| `helpers.py` | 1843-1858 | Populate `recovery_tag` on RETRY_OCR/RETRY_RTL gates **(expand per blocker above)**; add import-time coverage assertion | Set tags by `TreeDefect` name, not line number (line-drift risk noted in Zone 1) | Assertion must not fire for OK/CAP_MARGINAL/PERSIST_FAIL policy gates; RAISE-policy behavior must match Zone 2's resolved decision |
| `client.py` | 967-992 | Delete `_finalize_routing` entirely | Inline re-derivation (first_defect, route, `total_chars` recompute) into the recovery loop post-outcome step | `total_chars` recompute must be preserved — tree may have been replaced by a recovery |
| `client.py` | 1313-1483 | Refactor `_recover_ocr_escalation`: `ExtractionSnapshot`→`RecoveryOutcome` | Replace positional-tuple restore with `RecoveryOutcome` construction + `.apply()` | Retry-wins/retry-loses branching, `OCR_ESCALATION_TOTAL` metric, and the pre-retry `total_chars` comparison must be identical |
| `client.py` | 1485-1785 | Remove `route_overridden`/`original_gate_result` assignments from 5 recovery methods | 7 line deletions across `_recover_rtl_repair`, `_recover_rtl_flat_compare`, `_recover_vlm_fallback` (x2), `_recover_flat_prefer`, `_recover_landscape_reroute` | Guard conditions and route overrides (`state.route = Route.FLAT`) remain; metrics keep firing |
| `client.py` | 2197-2213 | Replace 7 sequential calls with declarative gate-driven loop **(extended per blocker above)** | Build `RECOVERY_DISPATCH` mapping tags→bound methods; loop over `GATES` entries with `recovery_tag`; re-derive `first_defect`/route after each outcome; run `flat_prefer`+`landscape_reroute` post-loop; assert every `recovery_tag` in `GATES` has a `RECOVERY_DISPATCH` entry | Execution order must match `GATES` table order; the loop must call each mapped recovery once per matching gate (e.g. `ocr_escalation` maps to both GARBLING and NODE_GARBLING) |
| `client.py` | 2006-2009,2057-2061 | `original_gate_result`→`state.gate_result` in `_persist_tree_result` (3 sites) | — | Semantically identical (both always held the same value after last revalidation) |

#### Wiring checks

| Symbol | Must be imported by / consumed where | Check type |
|---|---|---|
| `RecoveryOutcome` | `client.py` | import + call (`.apply`) |
| `GateSpec.recovery_tag` | `client.py` | dispatch |
| `RECOVERY_DISPATCH` | constructed **and referenced** (`dispatch[spec.recovery_tag](...)`) inside the recovery-loop body specifically | call |

> Per validation: `RECOVERY_DISPATCH` is specified as a local dict inside `CustomPageIndexClient.index()`, so an import-based check isn't possible — the check must confirm it is both built *and* referenced inside the loop body, not merely present anywhere in `client.py`. Consider promoting it to module level to make it independently testable, consistent with how `GateSpec.recovery_tag` is checked.

#### Test requirements

- `tests/test_zone3_recovery_tag_exhaustiveness.py` — every RETRY_OCR/RETRY_RTL gate with `gate_fn` has non-None `recovery_tag`; adding one without a tag raises `AssertionError` at import time.
- `tests/test_zone3_recovery_outcome_contract.py` — `RecoveryOutcome.apply` semantics (no-op on all-None, atomic multi-field apply, `rtl_decision` sentinel vs `None` distinction, frozen immutability).
- `tests/test_zone3_recovery_dispatch_coverage.py` — every `recovery_tag` in `GATES` has a `RECOVERY_DISPATCH` entry; each mapped function is callable once per matching `GATES` entry.
- `tests/test_zone3_recovery_loop_ordering.py` — identical state transitions vs the old 7-call pipeline for: GARBLING→ocr_escalation, RTL_REVERSAL→rtl_repair→rtl_flat_compare, NODE_COUNT_LOW→image_dominant_ocr **(only valid once the blocker's extended tag coverage lands)**, `ok=True`→flat_prefer+landscape_reroute, a recovery that resolves the defect prevents downstream recoveries firing on it, first_defect/route re-derived after each outcome.
- `tests/test_zone3_no_original_gate_result.py` — `ExtractionState` has neither `original_gate_result` nor `route_overridden`; `_persist_tree_result` uses `state.gate_result`.

#### Corpus validation

- Affected documents: `al-qarar-al-tanzimi.pdf`, `marsoom-13.pdf`, `human-rights.pdf`, `ward-597.pdf`, `cabinet_resolution.pdf`, `reitlehrer.pdf`, `cc4533aa.pdf`, `penal-code.pdf`
- Expected verdict direction: stable
- Spot-check count: 5

---

### Zone: Zone 4: Picture/OCR Recovery Dual-Path Conflation (wave 1, priority 4)

**Mechanism to eliminate:** Three-tier config inheritance (`OCR_ESCALATION`→`OCR_ESCALATION_GARBLE`+`OCR_ESCALATION_PER_PICTURE`) silently couples two independent features; dual skip-signaling (`decorative` bool + `skipped_reason` str) on `PictureResult` leaves downstream consumers unable to distinguish skip reasons; dual text-layer check path (`_text_layer_has_content` vs `_region_has_own_text_layer` gated by `_REGION_AWARE_TEXT_CHECK_ENABLED`) with a separate garble check gated by `_TEXT_LAYER_GARBLE_CHECK_ENABLED`; fragile `body_for_containment` parameter ordering in `pdf_to_markdown_docling` enforced only by comment, not structure.

> **RESOLVED (2026-08-18):** Wave 1 serialized into 1a→1b. Zone 4 now runs after Zone 1 lands. All `GarbleContext` references below rewritten to `profile=BULK_PROFILE` per Zone 1's post-fix API. Verify with codebase whether `OCR_ESCALATION_GARBLE`/`OCR_ESCALATION_PER_PICTURE` split in `config.py` is already partially landed before re-implementing Step 1.

**Strategy:** Deletion-first consolidation (validated pattern from Zones 1/3/5/6, zero regressions): delete the legacy `OCR_ESCALATION` flag and inheritance shim, leaving two flat independent env vars; delete `decorative` from `PictureResult`, unify all skip signaling through `skipped_reason`/`SkipReason`; collapse `_text_layer_has_content`/`_region_has_own_text_layer` into one function with optional `region_rect`, always-on garble check, deleting both rollback toggles; extract `body_for_containment` snapshot + fallback + recovery into a single function that structurally enforces ordering.

**Estimated complexity:** medium

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `config.py` | 39-63 | Delete legacy `OCR_ESCALATION` flag + inheritance shim | Flat independent reads: `OCR_ESCALATION_GARBLE`/`OCR_ESCALATION_PER_PICTURE` each default `True`, no ternary/inheritance | Setting one must not affect the other; both default `True` matching current `OCR_ESCALATION=1` behavior |
| `config.py` | 284-293 | Remove `ocr_escalation` key from `effective_config_snapshot` | Keep `ocr_escalation_garble`/`ocr_escalation_per_picture` keys | Key count decreases by 1 |
| `client.py` | 21-25 | Remove legacy `OCR_ESCALATION` import | Keep split-flag imports | Update stale comment at line 372 |
| `converters.py` | 1672-1681 | Delete `decorative` field from `PictureResult` TypedDict | `skipped_reason` is the unified mechanism | All other fields preserved exactly |
| `converters.py` | 2464-2471 | Replace `decorative=True` with `skipped_reason=SkipReason.OCR_MIN_CHARS.value` | Import `SkipReason` from `.picture_plane` | `OCR_MIN_CHARS` already in `_INTENTIONAL_SKIPS` — identical denominator/marker-strip semantics |
| `converters.py` | 2546-2547 | Remove `decorative` check from `splice_figure_markers` | `if result.get('skipped_reason'):` only | Identical marker-stripping behavior |
| `client.py` | 895-898 | Delete `decorative` propagation in `_enrich_image_blocks` | Delete the `if pr.get('decorative')` block | `skipped_reason` propagation must remain |
| `helpers.py` | 2089-2091 | Delete `decorative` check from `compute_image_enrichment_ratio` | `SkipReason`-based exclusion already covers it | `OCR_MIN_CHARS` must stay in `_INTENTIONAL_SKIPS` |
| `converters.py` | 1622-1624,1648-1650 | Delete `_TEXT_LAYER_GARBLE_CHECK_ENABLED`/`_REGION_AWARE_TEXT_CHECK_ENABLED` toggles | Both default `True`; garble+region checks become unconditional | Verify no deployment manifest sets either to `false` before merging |
| `converters.py` | 1765-1794 | Unify `_text_layer_has_content`/`_region_has_own_text_layer` | Single function, optional `region_rect`, always-on garble check **(rewrite as `profile=BULK_PROFILE` per Zone 1 dependency, not `GarbleContext.REGION`)** | `expected_script` passed through to `infer_script` |
| `converters.py` | 2299-2316 | Collapse dual text-layer check path to a single call | `has_own_text = _text_layer_has_content(page, region_rect=rect, expected_script=expected_script)` | Semantics identical to current default (`REGION_AWARE`+`GARBLE_CHECK` both True) |
| `converters.py` | 3575-3620 | Extract `_fallback_and_recover_pictures` to structurally enforce `body_for_containment` ordering | Snapshot pre-fallback md as local, run fallback stages, call `_recover_picture_results` with the pre-fallback snapshot, return `(post_fallback_md, pic_results, stage_records)` | Provenance records for all 3 stages preserved; snapshot must not be reachable by code that runs after the fallback stages |

#### Wiring checks

| Symbol | Must be imported by | Check type |
|---|---|---|
| `OCR_ESCALATION_GARBLE` | `client.py` | import |
| `OCR_ESCALATION_PER_PICTURE` | `client.py`, `converters.py` | import |
| `_text_layer_has_content` | `converters.py` | call |
| `_fallback_and_recover_pictures` | `converters.py` | call |
| `SkipReason` | `converters.py`, `helpers.py` | import |

#### Test requirements

- `tests/test_zone4_config_decouple.py` — `OCR_ESCALATION_GARBLE`/`OCR_ESCALATION_PER_PICTURE` fully independent; `config.py` has no `OCR_ESCALATION` attribute; `effective_config_snapshot` drops `ocr_escalation`; document/clarify the legacy-env-var backward-compat behavior explicitly.
- `tests/test_zone4_picture_result_normalization.py` — `PictureResult` has no `decorative` key (TypeError on construction with it); `splice_figure_markers`/`compute_image_enrichment_ratio` behave identically via `skipped_reason=OCR_MIN_CHARS`.
- `tests/test_zone4_text_layer_unified.py` — unified function correctness (region_rect on/off, always-on garble, min-chars floor); no rollback toggles exist; coverage-gate dispatch matches prior `REGION_AWARE=True` behavior.
- `tests/test_zone4_body_containment.py` — `_fallback_and_recover_pictures` snapshots pre-fallback text before running the fallback stages; containment check measures against pre-fallback text.
- `tests/test_zone4_wiring.py` — per wiring table above, plus confirms `_region_has_own_text_layer` no longer exists.

#### Corpus validation

- Affected documents: `cc4533aa` (picture-heavy), `marsoom-13` (Arabic scanned, text-layer garble path), `al-qarar-al-tanzimi` (scanned Arabic, coverage exemption path), `cabinet_resolution` (mixed content/image regions), `reitlehrer` (enrichment ratio affected by `decorative` removal)
- Expected verdict direction: stable
- Spot-check count: 5

---

### Zone: Zone 7: Silent Fallback Chains Masking Compliance and Quality Failures (wave 2, priority 5)

**Depends on:** Zone 4 (shares `converters.py`).

**Mechanism to eliminate:** Silent fallback chains where compliance-relevant (AGPL `pymupdf4llm`) and quality-relevant (tessdata Latin-only OCR) fallbacks fire at runtime with no Prometheus metric or extraction-metadata signal, masking both AGPL legal exposure (**CLAUDE.md Hard Rule 4**) and quality degradation (false-clean Latin mojibake on Arabic documents) behind reported job success. Five bugs: (1) AGPL `pymupdf4llm` actually processes a document as runtime fallback with no `AGPL_FALLBACK_TOTAL` counter tracking actual execution (only chain-composition counters exist); (2) `ensure_tessdata()` falls back to `["deu","eng"]` when requested languages are unavailable with only a `logger.warning`, no metric; (3) registry dual-write `_upsert_registry_row` swallows all exceptions at `logger.warning`, making staleness operationally invisible; (4) remote Docling stale-copy drift — **already addressed** by `_check_remote_docling_version` + `DOCLING_VERSION_SKEW` counter + `REMOTE_MD_RENORMALIZE` safety net, no new work; (5) `ALLOW_AGPL_FALLBACK` default `true` with no per-document alerting (addressed by fixing bug 1).

**Strategy:** Instrument-and-gate — observability-only, no behavioral or verdict changes. Add targeted Prometheus counters and log-level escalation at the 3 remaining silent-fallback sites so AGPL execution, tessdata quality degradation, and registry write failures are each independently alertable.

**Estimated complexity:** small

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `metrics.py` | 197-201 | Add `TESSDATA_LATIN_FALLBACK_TOTAL` counter | `prometheus_client.Counter`, label-free, placed after `AGPL_FALLBACK_TOTAL` | No name collisions |
| `client.py` | 83-95 | Add `AGPL_FALLBACK_TOTAL` to metrics import block | Insert as the **first** entry alphabetically (spec's "insert after BIDI_RENORM_SKIPPED" was backwards — `AGPL` sorts before `BIDI`) | Don't reorder existing imports |
| `client.py` | 1169-1177 | Increment `AGPL_FALLBACK_TOTAL.labels(reason='fired')` when `pymupdf4llm` actually handles a doc as runtime fallback | Inside the `state.used_converter != primary_name` branch, after the existing `logger.error` block, check `state.used_converter == 'pymupdf4llm'` and increment | Must NOT fire when `pymupdf4llm` is the *configured* primary (already covered by `operator_configured` label elsewhere) |
| `converters.py` | 1174-1176 | Increment `TESSDATA_LATIN_FALLBACK_TOTAL` when `ensure_tessdata` falls back to `['deu','eng']` | Lazy import inside the `if not available` block, before the return | No change to return value or the `TessdataUnavailableError` raise path for non-Latin languages |
| `worker.py` | 728-731 | Escalate registry dual-write failure log from WARNING to ERROR with `exc_info=True` | Change log level only | Must NOT re-raise — function contract is best-effort dual-write; existing `REGISTRY_WRITE_FAILURES_TOTAL` gauge and Redis mirror call unchanged |

#### Wiring checks

| Symbol | Must be imported by | Check type |
|---|---|---|
| `AGPL_FALLBACK_TOTAL` | `client.py` | import |
| `TESSDATA_LATIN_FALLBACK_TOTAL` | `converters.py` | call |

#### Test requirements

- `tests/test_zone7_silent_fallback.py` — `AGPL_FALLBACK_TOTAL.labels(reason='fired')` increments exactly once when `pymupdf4llm` fires as runtime fallback (docling primary raises); does NOT increment when `pymupdf4llm` is configured primary.
- Same file — `TESSDATA_LATIN_FALLBACK_TOTAL` increments on the `['deu','eng']` fallback path; does not increment on the happy path.
- Same file — registry dual-write failure logs at ERROR with `exc_info=True`; function does not raise; `REGISTRY_WRITE_FAILURES_TOTAL` still increments.
- Same file — regression: converter chain iteration still succeeds with fallback (metric instrumentation doesn't break the loop).
- Same file — regression: `ensure_tessdata` still raises `TessdataUnavailableError` for non-Latin languages when traineddata is missing (metric addition doesn't suppress the error).

#### Corpus validation

- Affected documents: `marsoom-13`, `cc4533aa`
- Expected verdict direction: stable
- Spot-check count: 2

---

## Validation Results

**Overall quality: `needs_work` — plan is NOT approved for dispatch as written.**

### Blockers (must fix before any fixer is dispatched)

1. **Zone 1 / Zone 4 (Wave 1) — file/API collision.** Both zones edit the same `converters.py:2299-2316` `GarbleContext.REGION` block; Zone 4's instructions tell the implementer to write against `GarbleContext.REGION`, a symbol Zone 1 deletes in the same wave. Both also touch `client.py`. Parallel dispatch as specified guarantees a merge conflict and a dangling-API bug. **Fix:** serialize Zone 1 → Zone 4, or rewrite Zone 4's targets in terms of Zone 1's post-fix `profile=BULK_PROFILE` API and declare `converters.py`+`client.py` as explicit shared files with a merge order. (Applied above in the Zone 4 section and Wave 1 rationale.)

2. **Zone 2 / Zone 3 — contradictory recovery-coverage contracts.** Zone 2 installs `_RECOVERY_REGISTRY` requiring RETRY_OCR/RETRY_RTL/RAISE gates to have recovery wiring; Zone 3 installs `GateSpec.recovery_tag` with a constraint that RAISE-policy gates must NOT require it — two overlapping mechanisms enforcing opposite invariants on the same gates, with Zone 3 never deleting or reconciling Zone 2's registry. This recreates the exact dual-authority sprawl these fixes exist to eliminate. **Fix:** drop `_RECOVERY_REGISTRY` from Zone 2 entirely; move the sole assertion into Zone 3's `GateSpec.recovery_tag`; make one explicit decision on whether RAISE gates need wiring and apply it identically in both specs. (Applied above — Zone 2 section marks `_RECOVERY_REGISTRY` removed.)

3. **Zone 3 — 3 of 7 recovery methods left unwired.** `recovery_tag` as specified covers only `ocr_escalation` and `rtl_repair`, plus 2 post-loop checks (`flat_prefer`, `landscape_reroute`). `_recover_rtl_flat_compare`, `_recover_vlm_fallback`, and `_recover_image_dominant_ocr` have no tag, no dispatch entry, and no post-loop slot — they would silently stop executing under this plan. The spec's own test requirement ("NODE_COUNT_LOW triggers image_dominant_ocr") contradicts its own code_targets (NODE_COUNT_LOW is RAISE-policy, gets no tag under the stated rule). **Fix:** extend `recovery_tag` coverage to all 7 methods (multi-tag support for RTL_REVERSAL, a tag for NODE_COUNT_LOW→image_dominant_ocr) or explicitly enumerate the 3 missing recoveries as ordered post-loop steps with guard conditions. (Flagged above in the Zone 3 section; **not yet resolved** — needs an explicit design decision before implementation.)

### Major issues

4. **Zone 2 / Zone 7 (Wave 2) — false "disjoint files" claim.** Both touch `client.py` (non-overlapping line ranges) and `converters.py`; Zone 7 also touches `metrics.py`, uninventoried anywhere in the wave. Not a guaranteed conflict but a coordination hazard and a factually wrong `shared_files: []` declaration. **Fix:** declare `client.py`+`converters.py` as shared with non-overlapping ownership, or serialize Zone 7 (small, observability-only) after Zone 2. (Applied above in Wave 2 rationale.)

### Minor issues (applied inline above)

5. Zone 2's `FLAT_GATE_SUBSET`/`_RECOVERY_REGISTRY` wiring checks pointed at their own definition file (`helpers.py`) rather than their consumer — same-file checks are vacuous. Corrected to require the symbol be referenced inside `compute_verdict`'s consuming branch, not merely defined.
6. Zone 3's `RECOVERY_DISPATCH` wiring check treated a local (function-scope) dict as an importable symbol. Corrected to require it be both built *and* referenced inside the recovery-loop body specifically.
7. Zone 2's `converters.py` wiring check forced an unused `VerdictResult` import. Dropped from that file's check — `_candidate_from_document` only needs `.verdict`.
8. Zone 1's phantom `GarbleProfile.evaluate` wiring check (no such method defined anywhere) removed entirely.
9. Small 1-2 line drift across multiple zones (client.py `check_garble` sites, `GATES` per-entry line anchors, `ExtractionSnapshot` span, AGPL fallback block). Recommendation applied: key `GATES` edits by `TreeDefect` name, not line number.
10. Zone 7's `client.py` import-ordering instruction was self-contradictory (told to insert `AGPL_FALLBACK_TOTAL` "after BIDI_RENORM_SKIPPED" while claiming alphabetical order, when `AGPL` sorts first). Corrected above.
11. Zone 2's `classify_verdict` backward-compat wrapper is explicitly kept alive for `preprocess_client.py`/`promotion_sweep.py` but had zero test coverage for that surface. A regression test requirement was added.

### Recommended sequencing before dispatch

1. Resolve blocker 2 (Zone 2/3 recovery-coverage mechanism) as a design decision — this affects both zone specs and must be settled once, not per-zone.
2. Resolve blocker 3 (Zone 3 missing recovery wiring) — extend `recovery_tag` coverage or add the explicit post-loop steps.
3. Apply blocker 1's Wave 1 serialization (Zone 1 → Zone 4) before any fixer touches `converters.py:2299-2316`.
4. Re-run zone-delta-analysis / triage against the corrected specs before dispatching fixers, per the project's standard zone-remediation pipeline (delta → triage → fix → verify).
