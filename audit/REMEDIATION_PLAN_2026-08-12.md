# Remediation Plan — 2026-08-12

**Audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-11_RUN-2.md
**Zones:** 5 of 7 (top by priority)
**Waves:** 3
**Validation status:** NOT APPROVED — needs_work (see Validation Results below; this plan has blocker-level conflicts that must be resolved before any wave executes)

---

## Priority Scores

| Zone | Score | Severity | Bug Count | Proposal Status | Excluded |
|---|---|---|---|---|---|
| Verdict engine: 11-gate first-match cascade + a second engine that re-derives the same signals | 72 | critical | 12 | partially_implemented | no |
| Six Arabic/RTL order deciders + 10-prong garble gate via 13 differently-shaped call sites | 54 | critical | 9 | partially_implemented | no |
| reason as both diagnosis and routing command inside the ~1,300-line index() | 48 | critical | 8 | partially_implemented | no |
| OCR escalation vs per-picture enrichment: mutually-exclusive subsystems joined by a fragile marker-count contract | 44 | critical | 11 | not_implemented | no |
| Verdict persistence: five writers, lost-update sidecar merge, verdict stored apart from its artifact | 28.8 | high | 8 | no_proposal | no |
| Flag and threshold sprawl: ~35 never-retired kill-switches with divergent binding times | 25.2 | high | 7 | no_proposal | no |
| pdf_to_markdown_docling: dual candidate pipelines and stage ordering encoded as line positions | 8.1 | high | 9 | implemented_and_wired | no |

Scoring formula: `severity_weight(critical=4, high=3) x bug_count x status_multiplier(not_implemented=1.0, no_proposal=1.2, partially_implemented=1.5, implemented_and_wired=0.3)`.

The plan below covers the top 5 zones by score (excludes the flag-sprawl and pdf_to_markdown_docling zones, which score lowest — the latter is reportedly already implemented and wired and warrants only a verification pass, not fresh remediation).

---

## Wave Sequence

### Wave 1
**Zones:**
- Verdict engine: 11-gate first-match cascade + a second engine that re-derives the same signals
- Verdict persistence: five writers, lost-update sidecar merge, verdict stored apart from its artifact

**Rationale:** Zone 1 (Verdict engine) rewrites helpers.py lines 141-201, 1387-1507, 1677-1844 to produce TreeDefect StrEnum, TreeSignals, TreeGateResult, and the declarative rule-table classify_verdict — foundational types consumed by Zones 3, 5, and 2. Zone 6 (Verdict persistence) touches ostensibly disjoint files (storage.py, registry.py, registry_backfill.py, worker.py, promotion_sweep.py, preprocess_client.py) and was intended to run in parallel on the premise that it consumes only the stable string output of classify_verdict.

**Shared files (declared):** none.

**⚠ Validation found this premise false.** Zone 6 in fact collides with Zone 1 on three files with contradictory edits (client.py:2102-2175, promotion_sweep.py:85, preprocess_client.py:289) — see Validation Results, blocker #1 and #2. Wave 1 as specified **cannot execute in parallel as written**; file ownership must be resolved (recommended: Zone 1 owns all three files' classify_verdict-call-site changes; Zone 6's conflicting targets there are dropped) before this wave starts.

---

### Wave 2
**Zones:**
- reason as both diagnosis and routing command inside the ~1,300-line index()
- Six Arabic/RTL order deciders + 10-prong garble gate via 13 differently-shaped call sites

**Rationale:** Zone 5 (reason routing) PRIMARY is client.py (lines 840-2195); it consumes TreeDefect and REASON_POLICY from Wave 1's Zone 1 to replace literal-string routing tuples with decide_route(). Its helpers.py touches were claimed to be light — importing already-landed types, not rewriting functions. Zone 3 (Arabic/RTL + garble) PRIMARY files are converters.py (RTL deciders at lines 110-1552) and helpers.py (garble_prongs at lines 1044-1113, gate functions at 1158, 1335), plus script.py; it depends on Zone 1's gate table to wire garble gates into the declarative structure.

**Shared files (declared):** `src/pageindex_mcp/helpers.py`

**⚠ Validation found the declared overlap understated.** Zone 5's code_targets in fact rewrite classify_verdict (helpers.py:1677-1727, 1743) and _flat_text_is_garbled (helpers.py:2892-2909) — not "light imports" — while Zone 3 concurrently rewrites garble_prongs and validate_tree in the same file in the same wave. Both zones also independently define a same-named `decide_route` function with incompatible signatures and both rewrite the same six client.py routing call sites (1266, 1548, 1590, 1637, 1780, 2066) with different expressions. **Wave 2 as specified will produce two conflicting patches to the same lines.** Recommended resolution: move Zone 5's helpers.py targets (classify_verdict typing, _flat_text_is_garbled typing) into Zone 1/Wave 1; give `decide_route` exactly one owner (Zone 5, returning the `Route` StrEnum) and one signature; Zone 1 must not touch client.py routing at all. See Validation Results blockers #3-#5.

---

### Wave 3
**Zones:**
- OCR escalation vs per-picture enrichment: mutually-exclusive subsystems joined by a fragile marker-count contract

**Rationale:** Zone 2 (OCR escalation) must run last because it heavily modifies both converters.py (lines 1581, 2236-2554 — picture/OCR plane) and client.py (lines 762-984, 1816, 1934 — OCR mode selection in index()). converters.py conflicts with Zone 3 from Wave 2, and client.py conflicts with Zone 5 from Wave 2. Zone 2 also depends on all prior waves: it needs Zone 1's TreeGateResult to wire enrichment scoring into the verdict, Zone 3's unified garble detection to determine when OCR escalation fires, and Zone 5's decide_route() to know which routing path controls OCR mode. Running it last means the verdict contract (Wave 1), garble detection (Wave 2), and routing logic (Wave 2) are all stable before the OCR subsystem is restructured on top of them.

**Shared files (declared):** `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py`, `src/pageindex_mcp/helpers.py`

**⚠ Note:** one of Zone 2's helpers.py targets (the `helpers.py:1768` `expected_script` fix on the `image_enrichment_promoted` garble check) duplicates a fix Zone 3 already makes in Wave 2. When Zone 2 executes, this target and its associated test (`test_zone2_garble_check_expected_script.py`) should be dropped or converted to a verify-only assertion that Zone 3's fix is present — see Validation Results, major issue on the OCR zone.

---

## Fix Specs

### Zone: Verdict engine: 11-gate first-match cascade + a second engine that re-derives the same signals (wave 1, priority 1)

**Mechanism to eliminate:** Dual-engine verdict derivation: `validate_tree` (helpers.py:1387-1507) is an 11-gate first-match cascade emitting one defect string, while `classify_verdict` (helpers.py:1677-1844) re-derives the same signals via `TreeSignals.from_tree` (helpers.py:171-201) and ORs its own `is_reordered` with the passed reason (line 1726). The two engines use different code paths on the same tree with no consistency check. GROUP-1 hard-fails in `classify_verdict` dispatch on bare string equality/startswith against the `validate_reason` parameter (lines 1716-1727), duplicating the gate semantics. `VerdictThresholds.from_env` (lines 141-156) re-reads ~10 env vars per call. Gate 11 (`arabic_low_content_ratio`, lines 1503-1506) is unreachable because gate 1 (line 1400) is a strict superset. Meanwhile, client.py routes on reason strings via six hand-maintained literal tuples/comparisons (lines 1256, 1475, 1548, 1590, 1637, 1780, 2066) that are never checked against the existing `REASON_POLICY` table (helpers.py:103-116), which is imported only by tests.

**Strategy:** Consolidate: (1) Delete dead gate 11. (2) Make `validate_tree` the single source of defect truth by enriching `TreeGateResult` with `TreeSignals`, eliminating the independent `TreeSignals.from_tree` re-derivation in `classify_verdict`. (3) Wire the existing-but-unwired `REASON_POLICY` table and `TreeDefect` enum into client.py as the dispatch mechanism, replacing all 7 literal-string routing sites. (4) Replace `classify_verdict`'s GROUP-1 string dispatches with a `HARD_FAIL_DEFECTS` frozenset. (5) Fix `original_reason` clobbering by making it an immutable `first_defect`. (6) Cache `VerdictThresholds` per-process.

**Estimated complexity:** large
**Severity:** critical

#### Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/helpers.py | 1503-1506 | Delete dead gate 11 (`arabic_low_content_ratio`) from `validate_tree` | Remove the 4-line if-block; gate 1 (line 1400) via `_tree_is_garbled` already tests the identical condition — gate 11 is a strict subset and unreachable. Keep `TreeDefect.ARABIC_LOW_CONTENT_RATIO` in the enum for backward-compat with persisted `verdict_reason` strings but mark it deprecated. | `TreeDefect.ARABIC_LOW_CONTENT_RATIO` must remain in the StrEnum so persisted `verdict_reason` values and Prometheus metric labels remain decodable. `REASON_POLICY` must still have a key for it. |
| src/pageindex_mcp/helpers.py | 1387-1507 | Extend `validate_tree` to return enriched `TreeGateResult` carrying the signals `classify_verdict` needs | Compute `TreeSignals` ONCE in `validate_tree` and attach to the returned `TreeGateResult` via a new `signals: TreeSignals \| None` field. Eliminates `classify_verdict`'s independent `TreeSignals.from_tree` call. | `TreeGateResult`'s `__iter__` backward-compat `(ok, reason_str)` must be preserved for existing tuple-unpacking call sites in client.py (lines 1238, 1341, 1487, 1575, 1697). `signals` must not appear in the iterator. |
| src/pageindex_mcp/helpers.py | 1677-1844 | Refactor `classify_verdict` to consume `TreeGateResult`/`TreeDefect` instead of bare `validate_reason` string | Change signature to accept `validate_result: TreeGateResult`. Replace GROUP-1 string dispatches (1716-1727) with a `HARD_FAIL_DEFECTS: frozenset[TreeDefect]` lookup. Remove the `TreeSignals.from_tree` call at 1710. Remove the OR at 1726. Add a backward-compat wrapper accepting `str` for `promotion_sweep.py`/`preprocess_client.py` callers during transition. | Verdict string output (`PASS`/`MARGINAL`/`FAIL`) and `verdict_reason` format must remain identical to today for all existing inputs. `_pass()` cap logic (bidi_degraded, depth_inadequate) must be preserved. |
| src/pageindex_mcp/helpers.py | 141-156 | Cache `VerdictThresholds` instead of re-reading env per call | Replace `VerdictThresholds.from_env()` with a module-level cache populated on first call; add `reset_verdict_thresholds()` for tests. | Tests manipulating env vars for threshold tuning must call `reset_verdict_thresholds()` in fixtures. `from_env` classmethod stays for backward-compat. |
| src/pageindex_mcp/helpers.py | 63-120 | Add `decide_route()` function and `HARD_FAIL_DEFECTS` set wired to `REASON_POLICY` | Add `decide_route(defect: TreeDefect, flat_doc_routing: bool = True) -> _ReasonPolicy` with `REASON_POLICY[defect]` lookup + exhaustiveness assert. Add `HARD_FAIL_DEFECTS` frozenset derived from `REASON_POLICY`. | **CONFLICT — see Validation blocker #4.** Zone 5 (wave 2) defines a same-named `decide_route` with an incompatible `Route`-returning signature. Resolve ownership before implementing either. |
| src/pageindex_mcp/client.py | multiple (1256-2076) | Replace all string-based reason routing with `TreeDefect` enum and `decide_route()` | Import `TreeDefect`, `TreeGateResult`, `REASON_POLICY`, `_ReasonPolicy`, `decide_route` from helpers; replace 7 literal-string comparison sites. | **CONFLICT — see Validation blocker #4.** Zone 5 also rewrites these exact lines. Recommend: this zone should NOT touch client.py routing; leave entirely to Zone 5. |
| src/pageindex_mcp/client.py | 1247, 1346, 1492, 1580, 1702, 2121 | Fix `original_reason` clobbering; thread `first_defect` correctly | Replace mutable `original_reason` with immutable `first_defect: TreeDefect` captured once; delete the four reassignments. | `first_defect` must represent the pre-retry defect. Six-variable atomic revert (1446-1455) must NOT be extended to include `first_defect` — it is immutable. **CONFLICT — see Validation blocker #5**: Zone 5's `ExtractionSnapshot` includes `first_defect` as a restored field, contradicting this constraint. |
| promotion_sweep.py | 81-86 | Fix stored `verdict_reason` being fed as `validate_reason` into `classify_verdict` | Parse `stored_reason` back to a `TreeDefect` via `TreeDefect.from_verdict_reason(stored_reason)` instead of passing the raw string. | **CONFLICT — see Validation blocker #1/#2.** Zone 6 instructs passing `None` instead at this same site. Pick one owner. |
| preprocess_client.py | 289 | Pass explicit `None` defect (or re-run `validate_tree`) instead of dropping all hard-fails | Re-run `validate_tree` on stored structure and pass its `TreeGateResult` to `classify_verdict`, rather than `None`. | **CONFLICT — see Validation blocker #1/#2.** Zone 6's test suite expects `None` input to still produce correct hard-fails at this site, which is incompatible with this target. |

#### Wiring Checks

| Symbol | Must be imported by | Check type |
|---|---|---|
| `decide_route` | src/pageindex_mcp/client.py | call |
| `TreeDefect` | src/pageindex_mcp/client.py, promotion_sweep.py, preprocess_client.py | dispatch |
| `TreeGateResult` | src/pageindex_mcp/client.py | import |
| `REASON_POLICY` | src/pageindex_mcp/client.py | dispatch |
| `_ReasonPolicy` | src/pageindex_mcp/client.py | dispatch |
| `HARD_FAIL_DEFECTS` | src/pageindex_mcp/helpers.py ⚠ vacuous — self-import, see Validation | dispatch |
| `reset_verdict_thresholds` | src/pageindex_mcp/helpers.py ⚠ vacuous — self-import, see Validation | call |

#### Test Requirements

- `tests/test_zone1_reason_enum.py` — exhaustiveness: every `TreeDefect` member has a `REASON_POLICY` entry; `HARD_FAIL_DEFECTS` ⊆ `REASON_POLICY` keys; `decide_route` returns a valid `_ReasonPolicy` for every `TreeDefect`.
- `tests/test_zone1_classify_verdict.py` — contract: `classify_verdict` accepts `TreeGateResult` and produces identical `(verdict, verdict_reason)` pairs as the old string-based path across `TreeDefect x content_class` combinations; regression on `is_reordered` dual-derivation elimination.
- `tests/test_zone1_dead_gate.py` — regression: gate 11 deleted; property test that any tree triggering `_is_garbled_blob`=True always hits gate 1 first.
- `tests/test_zone1_decide_route.py` — contract: `decide_route` matches current string-tuple dispatch for every `TreeDefect`.
- `tests/test_zone1_first_defect.py` — regression: `first_defect` never overwritten after initial capture, including across the RFC-030 D1 atomic-revert path.
- `tests/test_zone1_wiring.py` — wiring: client.py/promotion_sweep.py/preprocess_client.py import the new symbols; no literal reason strings remain in client.py routing logic (grep-based).
- `tests/test_zone1_verdict_thresholds_cache.py` — contract: `VerdictThresholds` cached after first call; `reset_verdict_thresholds()` clears it.

#### Corpus Validation
Affected: ward_597, siyasat_hawkama, GHV-TKV-Tarif, Reitlehrer, cabinet_resolution_no_21, federal_decree_law_no47, un_human_rights, federal_decree_law_13_2022, SLA_doc. Expected direction: improve. Spot-check count: 9.

---

### Zone: Six Arabic/RTL order deciders + 10-prong garble gate via 13 differently-shaped call sites (wave 2, priority 2)

**Mechanism to eliminate:** Arabic text orientation is decided six separate times (`_detect_arabic_reversal` at converters.py:110, `_text_is_logical_order` at converters.py:1442, `_heading_is_logical_order` at converters.py:1473, `reconstruct_bidi_order` at converters.py:1497, `_fix_residual_rtl_reversal` at converters.py:1552, plus gate-side `_tree_is_rtl_reversed` at helpers.py:1335 and `_check_bidi_coherence` at helpers.py:1158) with four sampling strategies and five divergent Arabic-ratio thresholds (0.15/0.3/0.4/0.5/0.30) — extraction can decide "already logical" while the gate later decides "reversed" on the same bytes. Underneath, `garble_prongs` (helpers.py:1044-1113) is called from 13 sites over incompatible blob shapes with inconsistent `expected_script` threading — three hole sites (converters.py:1746, converters.py:1844, helpers.py:1768) pass NO `expected_script`, unconditionally disabling the `latin_gibberish` prong. `reconstruct_bidi_order` applies a document-level boolean to the body but judges each heading individually, and `_fix_residual_rtl_reversal` runs at a different per-line threshold that can re-reverse lines the first pass deliberately left alone. `order_verdict` in script.py (lines 277-407) already exists as a unified RTL primitive but is imported only by tests, never production code.

**Strategy:** Consolidate all six RTL deciders into one `decide_rtl(text) -> RtlDecision` in script.py, backed by the existing `order_verdict` primitive. Add a `BlobKind` StrEnum to `garble_prongs` selecting a normalizer so raw markdown and tree text measure the same denominator. Make `expected_script` keyword-required (no default) on `garble_prongs` to close the three-site hole. Delete the five converter-side deciders, reduce `reconstruct_bidi_order` to a thin `apply_rtl` shim, delete `_tree_is_rtl_reversed`/`_check_bidi_coherence` from helpers.py. Shadow-test via `RTL_SINGLE_DECIDER` flag before wiring.

**Estimated complexity:** large
**Severity:** critical
**Depends on:** Verdict engine zone (Wave 1)

#### Code Targets

| File | What | How | Constraint |
|---|---|---|---|
| src/pageindex_mcp/script.py | Add `RtlDecision` dataclass, `decide_rtl()`, `BlobKind` StrEnum, `normalize_for_garble()`, `apply_rtl()` | `RtlDecision` frozen dataclass (`reversed`, `repair_effective`, `sampled`, `method`). `decide_rtl` wraps `order_verdict` with ONE threshold (0.15) and ONE sample count (8). `apply_rtl` is a single-pass best-candidate chooser (as-is / `get_display` / word-reversed, pick highest readability) applied uniformly to headings and body. `BlobKind.RAW_MARKDOWN` normalizer strips `#`/`\|`/HTML comments/whitespace before ratio computation. Export `GARBLE_DIGIT_FLOOR = 500`. | `order_verdict`'s existing signature/behavior must not change. script.py must stay dependency-free (no imports from helpers.py/converters.py). |
| src/pageindex_mcp/helpers.py | Make `expected_script` keyword-required on `garble_prongs`; add `blob_kind`; delete `_tree_is_rtl_reversed`, `_check_bidi_coherence`; update `validate_tree` to use `decide_rtl`; fix the no-`expected_script` hole at line 1768 | `garble_prongs(blob, *, expected_script, blob_kind=BlobKind.TREE_TEXT)`. `validate_tree`'s RTL/bidi gates dispatch on `decide_rtl(...)`. ⚠ Do NOT also delete gate 11 here — Zone 1 (Wave 1) already deletes it; anchor to function names, not the pre-Wave-1 line numbers listed in the raw proposal. | `garble_prongs` must return the same `frozenset[str]` prong names for backward compat. `TreeGateResult`/`TreeDefect` values must not change. |
| src/pageindex_mcp/converters.py | Delete `_detect_arabic_reversal`, `_text_is_logical_order`, `_heading_is_logical_order`, `_fix_residual_rtl_reversal`; reduce `reconstruct_bidi_order` to a shim calling `apply_rtl`; fix the two no-`expected_script` garble sites (1746, 1844) | `reconstruct_bidi_order(text, expected_script=None)` becomes a ~6-line call into `decide_rtl`/`apply_rtl`. Thread `expected_script` into `_text_layer_has_content` and the document-level text-layer fallback. | `reconstruct_bidi_order`'s external signature must remain callable as `reconstruct_bidi_order(text)` for client.py backward compat. NFKC-before-bidi ordering in `_pre_inference_normalize` preserved. |
| src/pageindex_mcp/client.py | Update `_detect_arabic_reversal` call sites to `decide_rtl`; pass `blob_kind=BlobKind.TREE_TEXT` on garble calls; stamp `decider_version` into persisted meta | Replace import and the two reversal-comparison call sites; add `decider_version` field (additive, non-breaking) to the meta dict at persistence. | `retry_wins` logic must produce identical outcomes for non-Arabic documents. |

#### Wiring Checks

| Symbol | Must be imported by | Check type |
|---|---|---|
| `RtlDecision` | converters.py, helpers.py, client.py | import |
| `decide_rtl` | converters.py, helpers.py, client.py | call |
| `apply_rtl` | converters.py | call |
| `BlobKind` | helpers.py, converters.py, client.py | dispatch |
| `normalize_for_garble` | helpers.py | call |
| `GARBLE_DIGIT_FLOOR` | helpers.py | import |

#### Test Requirements

- `tests/test_zone3_rtl_decision.py` — `decide_rtl` correctness across logical/visual/mirror-reversed/non-Arabic/bilingual/repaired cases; single threshold replaces five.
- `tests/test_zone3_apply_rtl.py` — no double-reversal; headings and body get the same decision.
- `tests/test_zone3_blob_kind.py` — `RAW_MARKDOWN` normalizer strips syntax correctly; `GARBLE_DIGIT_FLOOR` used, not a hardcoded literal.
- `tests/test_zone3_expected_script_required.py` — `garble_prongs` raises `TypeError` without `expected_script`; all 13 production call sites pass it explicitly.
- `tests/test_zone3_garble_hole_sites.py` — regression on the three formerly-unprotected call sites using ward_597-class input.
- `tests/test_zone3_validate_tree_rtl.py` — `validate_tree`'s RTL/bidi gates use `decide_rtl`.
- `tests/test_zone3_reconstruct_bidi_shim.py` — shim output matches old implementation on corpus cases.
- `tests/test_zone3_decider_version_stamp.py` — meta dict carries `decider_version` additively.

*Note: recommend renaming these test files off the `test_zone3_*` prefix to avoid collision/confusion with the OCR zone's `test_zone2_*` files and the pre-existing `tests/test_zone5_script.py` — see Validation Results, minor issue.*

#### Corpus Validation
Affected: ward_597 (expect PASS→FAIL via latin_gibberish now firing), siyasat_hawkama (expect PASS→MARGINAL/FAIL), un_human_rights (expect MARGINAL), federal_decree_law_13_2022 (expect MARGINAL), GHV-TKV-Tarif (German, expect stable PASS), Reitlehrer (German, expect stable), cabinet_resolution_no_21 (verify consistent heading+body treatment). Expected direction: improve. Spot-check count: 7.

---

### Zone: reason as both diagnosis and routing command inside the ~1,300-line index() (wave 2, priority 3)

**Mechanism to eliminate:** The variable `reason` in client.py:`index()` serves simultaneously as (1) a diagnostic label written by `validate_tree`, (2) a routing command (recovery branches assign `reason='node_count<3'` to a PASSING tree purely to hijack the flat-routing whitelist), and (3) an input to `classify_verdict` that determines the final verdict. Recovery blocks clobber both `reason` and `original_reason` (4 reassignments at lines 1346, 1492, 1580, 1702), the RFC-030 D1 atomic revert (1446-1455) restores six variables but omits `original_reason`, and six literal-string tuples (1266, 1548, 1590, 1637, 1780, 2066) hand-duplicate the routing logic that the landed-but-unwired `REASON_POLICY` table already encodes. Consequence: new `TreeDefect` values silently hard-fail documents, re-route overrides persist a defect the document does not have, and a lost retry leaves the garble-by-default protection evaporated because `original_reason` is `''` while `reason` was reverted to `'garbling'`.

**Strategy:** Split the single `reason` variable into three typed fields with one writer each: (1) `defect: TreeDefect` written only by `validate_tree`, (2) `first_defect: TreeDefect` written once and never overwritten, (3) `route: Route` StrEnum (TREE/FLAT/REJECT/PERSIST_FAIL) determined by a pure `decide_route(defect, flags) -> Route` function dispatching via `REASON_POLICY`. Collapse the six pre-retry snapshot variables into one frozen `ExtractionSnapshot` dataclass. Wire `TreeDefect`/`REASON_POLICY` into production routing, replacing all literal-string tuple checks.

**Estimated complexity:** large
**Severity:** critical
**Depends on:** Verdict engine zone (Wave 1)

**⚠ Ownership note (see Validation blockers #3-#5):** This zone and Zone 1 currently both define `decide_route` and both rewrite the same six client.py routing lines and the same `classify_verdict`/`_flat_text_is_garbled` signatures in helpers.py, with incompatible contracts. Before implementation: this zone should be the SOLE owner of `decide_route` (returning the `Route` StrEnum, not `_ReasonPolicy` directly) and the SOLE owner of all client.py routing rewrites; Zone 1 must drop its client.py routing targets and its competing `decide_route` definition. `first_defect` must NOT be a field of `ExtractionSnapshot` (contradicts Zone 1's immutability constraint) — keep it external to the snapshot, captured once and threaded separately.

#### Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/helpers.py | 94-120 | Add `Route` StrEnum and `decide_route()` | `Route = StrEnum('Route', 'TREE FLAT REJECT PERSIST_FAIL')`; `decide_route(defect, flat_routing_enabled) -> Route` with exhaustive `REASON_POLICY` lookup and exhaustiveness assert. | `TreeDefect.value` strings stay byte-identical to current reason strings. `REASON_POLICY` table contents unchanged. Single definition — supersedes Zone 1's competing `decide_route`. |
| src/pageindex_mcp/helpers.py | 78-92 | Add `ExtractionSnapshot` frozen dataclass | Fields: `result`, `ok`, `defect`, `md_content`, `pic_results`, `used_converter`, `tmp_md_path` (NOT `first_defect` — see ownership note above). | Must include ALL mutable variables the current six-variable revert restores. Frozen to prevent post-capture mutation. |
| src/pageindex_mcp/client.py | 1247, 1346, 1492, 1580, 1702, 1825, 2121 | Replace `original_reason = reason` with write-once `first_defect` | Capture `first_defect = result_gate.defect` once after the first `validate_tree` call; delete the 4 reassignments; thread unchanged to `_flat_text_is_garbled` and `classify_verdict`. | Garble-by-default protection in `_flat_text_is_garbled` must receive the TRUE first-pass defect. |
| src/pageindex_mcp/client.py | 1266, 1548, 1590, 1637, 1780, 2066 | Replace six literal-string routing tuples with `REASON_POLICY`/`decide_route()` dispatch | Import `TreeDefect`, `Route`, `decide_route`, `REASON_POLICY`, `ExtractionSnapshot` from helpers; replace each tuple check with the typed equivalent. | Behavior-identical: same `TreeDefect` values trigger each branch as today. |
| src/pageindex_mcp/client.py | 1535, 1602, 1628, 1741, 1769 | Replace re-route overrides (`reason='node_count<3'` on passing trees) with `Route.FLAT` that leaves `defect`/`ok` untouched | Set `route = Route.FLAT` without mutating `ok`/`defect`; flat-routing whitelist checks `route == Route.FLAT`. | Stored `verdict_reason` must no longer be a fabricated `'node_count<3'` for a tree that actually passed. |
| src/pageindex_mcp/client.py | 1275-1290, 1446-1462 | Replace six-variable pre-retry snapshot with `ExtractionSnapshot` | Single `pre_retry = ExtractionSnapshot(...)`; revert destructures from it. | All currently-snapshotted fields included; revert restores atomically; `tmp_md_path` cleanup preserved. |
| src/pageindex_mcp/helpers.py | 2892-2909 | Update `_flat_text_is_garbled` to accept `TreeDefect` instead of string | `original_reason: TreeDefect \| None = None`; check against `TreeDefect.GARBLING`/`NODE_GARBLING`. | Backward compatible with `None` input. |
| src/pageindex_mcp/helpers.py | 1677-1727, 1743 | Update `classify_verdict` to accept `TreeDefect` instead of string for `validate_reason` | Replace six string comparisons with `TreeDefect` enum comparisons. | promotion_sweep.py and preprocess_client.py callers updated in lockstep — **coordinate with Zone 1's competing instructions for these same two files; resolve to one owner before implementing (Validation blocker #1/#2).** |
| promotion_sweep.py | 81-87 | Convert stored `verdict_reason` string to `TreeDefect` before calling `classify_verdict` | `TreeDefect(stored_reason) if stored_reason in TreeDefect._value2member_map_ else None`. | Must gracefully fall through to `None` for legacy/unrecognized strings. |
| preprocess_client.py | 289 | Verify type-compatibility of existing `None` call | No behavioral change; confirm `None` is valid under the new `TreeDefect \| None` annotation. | No behavioral change intended. |

#### Wiring Checks

| Symbol | Must be imported by | Check type |
|---|---|---|
| `Route` | client.py | dispatch |
| `decide_route` | client.py | call |
| `TreeDefect` | client.py, promotion_sweep.py | dispatch |
| `REASON_POLICY` | client.py | dispatch |
| `ExtractionSnapshot` | client.py | call |
| `_ReasonPolicy` | client.py | dispatch |

#### Test Requirements

- `tests/test_zone5_route_enum.py` — `Route` exhaustiveness across every `TreeDefect` member; no `KeyError`.
- `tests/test_zone5_extraction_snapshot.py` — snapshot is frozen; field set matches current revert-block variables (minus `first_defect`); roundtrip integrity.
- `tests/test_zone5_first_defect_immutable.py` — AST/grep scan: `first_defect =` appears exactly once in client.py.
- `tests/test_zone5_reroute_no_fake_defect.py` — re-routed passing trees do not persist a fabricated `verdict_reason`.
- `tests/test_zone5_classify_verdict_typed.py` — `classify_verdict` accepts `TreeDefect` and produces identical output to string-based path, parametrized over all `TreeDefect` values plus `None`.
- `tests/test_zone5_promotion_sweep_compat.py` — string→`TreeDefect` conversion handles valid/None/empty/legacy strings gracefully.
- `tests/test_zone5_garble_default_protection.py` — garble-by-default protection locked in for `GARBLING`/`NODE_GARBLING`/`None`.

*Note: rename these away from the `test_zone5_*` prefix or confirm no collision with the pre-existing `tests/test_zone5_script.py` before creating them — see Validation Results.*

#### Corpus Validation
Affected: ward_597, ghv_tkv_tarif, sla_doc, marsoom_13_2022, alqarar_altanzimi, siyasat_hawkama, uae_numbers_portrait, uae_numbers_landscape, reitlehrer. Expected direction: improve. Spot-check count: 9.

---

### Zone: OCR escalation vs per-picture enrichment: mutually-exclusive subsystems joined by a fragile marker-count contract (wave 3, priority 4)

**Mechanism to eliminate:** Page-level OCR escalation (`force_full_page_ocr`) and per-picture crop-OCR/VLM enrichment are structurally incompatible subsystems gated by one shared boolean flag (`_OCR_ESCALATION`, duplicated verbatim in client.py:344 and converters.py:1581), exchanging state through: (1) an all-or-nothing marker-count guard (`splice_picture_text_for_tree` at converters.py:2474) that abandons ALL picture OCR when marker count != number of real pictures, (2) a destructive `pop('ocr_text')` mutation in `splice_figure_markers` (converters.py:2552-2554) that empties shared `PictureResult` dicts before `_enrich_image_blocks` reads them, making verdict depend on call-site ordering, (3) literal-string filtering of fabricated `'landscape_fallback_picture'` entries at two independent sites, and (4) untyped `skipped_reason` strings that silently reshape the enrichment denominator and thereby the ≥0.8 `image_enrichment_promoted` PASS rescue. The D3a probe is a log-only no-op on the default path, and the MAX_FULLPAGE cap fires before the coverage exemption, so Docling region enumeration order decides which pages get OCR.

**Strategy:** Extract a new `picture_plane.py` module with typed contracts: `OcrMode` StrEnum (NONE/FULL_PAGE/PER_PICTURE) chosen by a single `decide_ocr_mode()`; `SkipReason` StrEnum with a `counts_in_enrichment_denominator` policy column; `PictureRegion` dataclass with a `spliced_into_markdown` flag replacing the destructive pop; `bind_markers()` that aligns markers to regions per-marker instead of all-or-nothing. Consolidate the duplicated `_OCR_ESCALATION` into config.py. Delete the dead D3a probe and the fabricated-string filters. Reorder the coverage exemption before the MAX_FULLPAGE cap check.

**Estimated complexity:** large
**Severity:** critical
**Depends on:** Verdict engine zone (Wave 1), RTL/garble zone and reason-routing zone (Wave 2)

**⚠ Duplication note (see Validation Results, major issue):** the `helpers.py:1768` `expected_script` fix on the `image_enrichment_promoted` garble check is already made by the RTL/garble zone in Wave 2. When this zone executes, drop that target and its test (`test_zone2_garble_check_expected_script.py`), or convert it to a verify-only assertion.

#### Code Targets

| File | What | How | Constraint |
|---|---|---|---|
| src/pageindex_mcp/picture_plane.py (new) | `OcrMode`, `SkipReason` (with `counts_in_denominator` property), `PictureRegion` dataclass, `decide_ocr_mode()`, `bind_markers()` | `OcrMode = StrEnum(... NONE FULL_PAGE PER_PICTURE)`. `SkipReason` members: PAGE_COVERAGE, CLIP_TEXT_ALREADY_EXPORTED, DECORATIVE_ICON, LANDSCAPE_FALLBACK, OCR_MIN_CHARS, MAX_FULLPAGE_CAP, UNKNOWN. `decide_ocr_mode(pre_garbled, pre_garble_force_ocr_enabled, inspector_force_ocr, image_dominant, ocr_escalation_enabled) -> OcrMode` encoding mutual exclusion. `bind_markers()` replaces markers per-marker, never bailing out entirely on count mismatch. | Pure-logic module, no imports from client.py/converters.py (avoid circularity); may import config.py/helpers.py only. |
| src/pageindex_mcp/config.py | Consolidate `_OCR_ESCALATION` into one canonical `OCR_ESCALATION` constant | `OCR_ESCALATION = _envbool('OCR_ESCALATION', '1')`; export it; reference from `effective_config_snapshot()`. | Default value unchanged; existing snapshot key name (`ocr_escalation`) unchanged. |
| src/pageindex_mcp/client.py | Import `OCR_ESCALATION` from config; replace four force-OCR decision branches with one `decide_ocr_mode()` call; replace destructive pop with `spliced_into_markdown` flag | Dispatch on `ocr_mode` at the garble-escalation and image-dominant-escalation gates; pass `PictureRegion` lists to splice/enrich/ratio calls. | Must not change flat-routing success-path behavior; must not break the six-variable atomic revert (pic_results is part of the snapshot). |
| src/pageindex_mcp/converters.py | Delete local `_OCR_ESCALATION` duplicate; replace `'landscape_fallback_picture'` string filters with `SkipReason.LANDSCAPE_FALLBACK`/`PictureRegion.kind`; refactor splice functions to use `PictureRegion`; reorder coverage exemption before MAX_FULLPAGE cap; delete destructive `pop('ocr_text')` | In `splice_picture_text_for_tree`, replace all-or-nothing bail with `bind_markers()`. In `splice_figure_markers`, delete the pop; set `region.spliced_into_markdown = True` instead. In `_recover_picture_text`, move the coverage-exemption check before the MAX_FULLPAGE cap check. | Keep `PictureResult` TypedDict temporarily for backward compat during migration. Must not change the Tesseract OCR path or `_clip_text_contained` behavior. |
| src/pageindex_mcp/helpers.py | Refactor `compute_image_enrichment_ratio` to use `SkipReason` policy instead of raw string checks | For each block with a `SkipReason`, use `counts_in_denominator` to decide inclusion in numerator/denominator. | Must not change the 0.8 `image_enrichment_promoted` threshold or scoring logic for blocks with enrichment data. |
| src/pageindex_mcp/client.py | Delete the dead D3a pre-conversion probe on the default (disabled) path | Extract `_probe_pdf_page_count()` (~10 lines) for page-count acquisition only; delete the unused garble-probe logic. | Preserve `pdf_page_count` acquisition (feeds `validate_tree`'s suspect_density gate) and the `ALLOW_AGPL_FALLBACK` gate on fitz import. |

#### Wiring Checks

| Symbol | Must be imported by | Check type |
|---|---|---|
| `OcrMode` | client.py, converters.py | dispatch |
| `SkipReason` | converters.py, helpers.py, client.py | isinstance |
| `PictureRegion` | converters.py, client.py | import |
| `decide_ocr_mode` | client.py | call |
| `bind_markers` | converters.py | call |
| `OCR_ESCALATION` | client.py, converters.py | import |

#### Test Requirements

- `tests/test_zone2_picture_plane.py` — `OcrMode`/`SkipReason` exhaustiveness and mutual-exclusion contract for `decide_ocr_mode`.
- `tests/test_zone2_bind_markers.py` — per-marker alignment across count-match, count-excess, count-deficit, and FABRICATED-region-exclusion cases.
- `tests/test_zone2_enrichment_ratio.py` — `SkipReason`-typed denominator behavior, including `spliced_into_markdown=True` blocks still counting as enriched.
- `tests/test_zone2_no_destructive_pop.py` — regression: `ocr_text` remains readable post-splice; `spliced_into_markdown` flag set correctly.
- `tests/test_zone2_ocr_escalation_duplicate.py` — wiring: `_OCR_ESCALATION` exists only in config.py; grep finds zero duplicate `os.getenv` hits elsewhere.
- `tests/test_zone2_coverage_exemption_order.py` — coverage exemption fires before MAX_FULLPAGE cap regardless of Docling enumeration order.

*Drop `tests/test_zone2_garble_check_expected_script.py` — duplicates Zone 3's fix; see duplication note above.*

#### Corpus Validation
Affected: وارد 597, سياسة حوكمة, GHV-TKV-Tarif.pdf, uae_numbers_portrait.pdf, uae_numbers_landscape.pdf, مرسوم 13/2022, القرار التنظيمي, cabinet_resolution_no_21. Expected direction: improve. Spot-check count: 8.

---

### Zone: Verdict persistence: five writers, lost-update sidecar merge, verdict stored apart from its artifact (wave 1, priority 5)

**Mechanism to eliminate:** Five independent verdict writers (client.py:2175 ingest, storage.py:287 `save_flat_doc` implicit, `registry_backfill._enrich_one`/`_heal_one` with no verdict, `promotion_sweep.py`:105 and `preprocess_client.py`:305 recomputing with wrong/absent inputs) all funnel into `save_doc_meta` (storage.py:508), a read-merge-write with no ETag/CAS concurrency control. The verdict lives ONLY in the sidecar/registry, never in `processed/<id>.json` — the file the registry dual-write actually reads. This causes: (1) lost updates by construction when reconcile cron, orphan healer, completing ingest, sweep, and recompute script race on the same key; (2) a two-phase non-atomic artifact write (`save_doc` then `save_doc_meta`) that `registry_backfill` classifies as ORPHAN and repairs from a source lacking the verdict; (3) tree payload has no verdict key, so `read_registry_fields` upserts `verdict=''`, and `'' != 'FAIL'` passes the WHERE filter, making FAIL tree docs queryable until reconcile catches up; (4) `promotion_sweep.py` feeds stored `verdict_reason` into `classify_verdict`'s `validate_reason` causing sticky permanent FAILs from prefix-matching; `preprocess_client.py` passes `validate_reason=None`, dropping every hard fail.

**Strategy:** Consolidate all verdict writes into a single `write_verdict` entry point; embed verdict in the processed artifact; add a temporal CAS guard to `save_doc_meta`; fix parameter-namespace conflation in `promotion_sweep`/`preprocess_client`; add `verdict_computed_at` to Postgres for temporal conflict resolution.

**Estimated complexity:** large
**Severity:** high

**⚠ Path and sequencing corrections required before implementation (see Validation Results, blockers #1-#2):**
1. `promotion_sweep.py` and `preprocess_client.py` are repo-root scripts, not `src/pageindex_mcp/promotion_sweep.py` (which does not exist). All code_targets and wiring checks below must reference the corrected root-level paths.
2. This zone is placed in Wave 1 but its own `depends_on` names the reason-routing zone (Wave 2) — a forward dependency that cannot be satisfied by Wave 1 scheduling. **Recommend moving this zone to Wave 3** (after Zone 1 and the reason-routing zone land), or dropping the dependency and pinning explicitly to Zone 1's `classify_verdict` interface only.
3. Its `promotion_sweep.py`/`preprocess_client.py` targets directly contradict Zone 1's targets for the same two files/lines (Zone 1: parse via `TreeDefect.from_verdict_reason` / re-run `validate_tree`; this zone: pass `None`). Pick one owner — recommended: Zone 1 owns both files' `classify_verdict`-call-site fix; this zone's conflicting instructions there are dropped in favor of Zone 1's.

#### Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/storage.py | ~600 | New `write_verdict()` atomically writing verdict fields to both `processed/<id>.json` (or `.flat.json`) AND `meta.json` | `write_verdict(doc_id, verdict, verdict_reason, pipeline_version, verdict_computed_at, max_leaf_ratio, content_class=None, extra_meta=None)`: read existing artifact, inject verdict fields, re-write; then call `save_doc_meta` with the full meta; both writes use `_confirm_write_visible`. | Must not change `processed/<id>.json` shape beyond adding verdict fields. `save_doc_meta`'s read-merge-write semantics preserved for non-verdict fields. |
| src/pageindex_mcp/storage.py | 508-597 | CAS guard on `save_doc_meta` to detect concurrent sidecar clobber | Compare `existing.get('verdict_computed_at')` vs incoming; if existing is newer and incoming carries a verdict field, log WARNING and skip the verdict fields in the merge (soft CAS). Consider extracting this into a named `_verdict_cas_guard()` function for testability (per Validation major issue). | Must not reject non-verdict field updates. Only verdict/verdict_reason/pipeline_version/verdict_computed_at/max_leaf_ratio get the temporal guard. |
| src/pageindex_mcp/client.py | 2102-2175 | Include verdict fields in the `save_doc` payload so the artifact always carries the verdict | Move the `classify_verdict` call before `save_doc`; include verdict fields in the `save_doc` payload dict. **Coordinate with Zone 1, which rewrites this same `classify_verdict` call site to pass `first_defect`/`TreeGateResult` — implement as one combined change, not two independent ones.** | `prior_verdict` lookup must also move before `save_doc`. Must not change the final verdict value — only ordering. |
| src/pageindex_mcp/storage.py | 646-651 | `read_registry_fields` falls back to sidecar for verdict fields when absent from the processed doc | Defensive fallback only when the processed doc lacks the verdict field; do not add a second MinIO GET on the happy path. | Once the client.py artifact-carries-verdict change lands, this becomes a legacy-doc protection only. |
| registry_backfill.py | ~333-345 (verify against current file; audit's cited 339 may have drifted) | `_heal_one` includes verdict fields from the sidecar when available | If `read_registry_fields` result lacks verdict fields, attempt sidecar read and merge before `save_doc_meta`/`upsert_doc`. | Graceful degradation preserved if sidecar is also missing. |
| src/pageindex_mcp/registry.py | 160-186 | Tighten upsert `COALESCE` to prefer the newer `verdict_computed_at` | Add `verdict_computed_at` column + migration; `CASE WHEN EXCLUDED.verdict_computed_at >= COALESCE(doc_registry.verdict_computed_at,'') THEN EXCLUDED.verdict ELSE COALESCE(NULLIF(EXCLUDED.verdict,''), doc_registry.verdict) END`. | Requires idempotent schema migration. Existing rows with NULL `verdict_computed_at` must still accept any incoming verdict. |

#### Wiring Checks

| Symbol | Must be imported by | Check type |
|---|---|---|
| `write_verdict` | src/pageindex_mcp/client.py, promotion_sweep.py (repo root — corrected path) | call |
| `_verdict_cas_guard` | src/pageindex_mcp/storage.py — **vacuous as a self-import check; convert to a functional test that the guard fires (see Validation)** | call |
| `verdict_computed_at` column | src/pageindex_mcp/registry.py — **not an importable symbol; convert to a migration-exists assertion (see Validation)** | dispatch |

#### Test Requirements

- `tests/test_zone6_verdict_persistence.py` — atomic dual-write contract (artifact + sidecar agree); CAS guard preserves newer verdict; promotion_sweep no-sticky-FAIL regression *(pending resolution of the ownership conflict with Zone 1 — see note above)*; `read_registry_fields` returns non-empty verdict from the artifact; `_heal_one` produces non-empty-verdict rows from sidecar; Postgres upsert prefers newer `verdict_computed_at`; preprocess_client recompute does not silently promote gate-rejected docs.

#### Corpus Validation
Affected: ward_597, sla_doc, ghv_tkv_tarif, cabinet_resolution_no_21, federal_decree_law_no_47. Expected direction: improve. Spot-check count: 5.

---

## Validation Results

**Overall quality: needs_work — plan is NOT approved for execution as written.**

The scoring, zone selection, and per-zone technical analysis are sound, but the wave sequencing and cross-zone code_targets contain multiple blocker-level contradictions that would cause two parallel or sequential fix agents to overwrite each other's work, or to target nonexistent files. These must be resolved (ownership reassigned, paths corrected, dependency graph re-sequenced) before any wave is dispatched to execution.

### Blockers

1. **Wrong path for promotion_sweep.py.** The Verdict-persistence zone's code_target and wiring check reference `src/pageindex_mcp/promotion_sweep.py`, which does not exist (`ls` confirms). The real file is `promotion_sweep.py` at the repo root. The `write_verdict` wiring check can never pass as specified.
   *Fix:* Correct the path to repo-root `promotion_sweep.py`; either add a code_target routing promotion_sweep's write through `write_verdict`, or drop it from `write_verdict`'s `must_be_imported_by` list.

2. **Wave-dependency inversion.** The Verdict-persistence zone is scheduled in Wave 1 but its `depends_on` names the reason-routing zone, which runs in Wave 2 — a dependency cannot land after its dependent. `depends_on` strings also don't match the exact `zone_name` format used elsewhere, breaking machine matching of the dependency graph.
   *Fix:* Move this zone to Wave 3 (after the verdict-engine and reason-routing zones), or drop the forward dependency and pin explicitly to the `classify_verdict` interface; normalize `depends_on` to exact `zone_name` strings.

3. **Wave 1 file-collision contradiction (verdict engine vs verdict persistence).** Declared `shared_files: []` and "entirely disjoint files," but the two zones collide on three files with contradictory edits: (a) client.py:2102-2175 — persistence zone moves the `classify_verdict` call before `save_doc`, engine zone rewrites the same call to pass `first_defect`; (b) promotion_sweep.py:85 — engine zone says parse via `TreeDefect.from_verdict_reason()`, persistence zone says pass `None`; (c) preprocess_client.py:289 — engine zone says re-run `validate_tree` and pass its `TreeGateResult`, persistence zone's test expects `None` input to still produce hard-fails (impossible without the engine zone's change, and obsolete with it).
   *Fix:* Pick one owner per file — engine zone is the natural owner (it defines the new `classify_verdict` interface); delete persistence zone's conflicting targets; update Wave 1's `shared_files` list or re-sequence.

4. **Duplicate, incompatible `decide_route` definitions (reason-routing zone vs verdict-engine zone).** Both define a same-named `decide_route` in the same helpers.py region with incompatible contracts — engine zone: `decide_route(defect, flat_doc_routing=True) -> _ReasonPolicy`; reason-routing zone: `decide_route(defect, flat_routing_enabled) -> Route` (new StrEnum). Both wire it into client.py and both replace the same six literal tuples (1266, 1548, 1590, 1637, 1780, 2066) with different expressions.
   *Fix:* Merge into one design — `Route` is the better abstraction; `_ReasonPolicy` stays internal. `decide_route` lives in exactly one spec with one signature/return type. Engine zone should NOT touch client.py routing at all; reason-routing zone owns all client.py routing replacement.

5. **Wholesale duplication between reason-routing zone and verdict-engine zone.** Both introduce `first_defect` at client.py:1247, both delete the same four `original_reason` reassignments (1346, 1492, 1580, 1702), both thread it to `classify_verdict` at 2121, and both change `classify_verdict`'s parameter incompatibly (engine zone: accept `TreeGateResult`; reason-routing zone: accept `TreeDefect | None`). Reason-routing zone's mechanism also claims `REASON_POLICY` is "imported only by tests," which becomes false once its own declared Wave-1 dependency lands.
   *Fix:* De-duplicate — engine zone owns helpers.py (`classify_verdict` signature, `HARD_FAIL_DEFECTS`, thresholds cache, dead gate); reason-routing zone owns client.py (`first_defect`, routing, snapshot). State one `classify_verdict` signature once.

6. **Direct constraint contradiction on `first_defect` and `ExtractionSnapshot`.** Engine zone states the six-variable atomic revert "must NOT be extended to include `first_defect` — it is immutable," while reason-routing zone's `ExtractionSnapshot` dataclass includes a `first_defect` field that is captured and restored by the revert destructuring. Both cannot be implemented as specified.
   *Fix:* Adopt engine zone's model (`first_defect` is write-once and lives outside the snapshot); remove `first_defect` from `ExtractionSnapshot`'s field list and from the field-count regression test.

### Major Issues

7. **Vacuous wiring checks (verdict-engine zone).** `HARD_FAIL_DEFECTS` and `reset_verdict_thresholds` both list `must_be_imported_by: ['src/pageindex_mcp/helpers.py']` — but both symbols are *defined* in helpers.py. A module cannot import its own symbol; an import-grep wiring gate proves nothing here. This repeats the "landed-but-unwired" failure mode the audit already found for `REASON_POLICY`.
   *Fix:* Point `HARD_FAIL_DEFECTS`'s check at its real consumer (`classify_verdict` call-path assertion or AST check); point `reset_verdict_thresholds`'s check at the test files/conftest that must call it.

8. **Two unverifiable wiring checks (verdict-persistence zone).** `_verdict_cas_guard` `must_be_imported_by` storage.py has no corresponding code_target creating that symbol (the CAS is specified as inline logic inside `save_doc_meta`), and a self-import check is vacuous regardless. `verdict_computed_at column` with `check_type: dispatch` is not an importable Python symbol at all.
   *Fix:* Either extract the CAS into a named `_verdict_cas_guard` function as an explicit code_target, or replace both checks with testable assertions (a test that the guard fires; a migration-exists check for the column).

9. **Duplicated dead-gate-11 deletion instruction (RTL/garble zone).** The helpers.py target explicitly instructs deleting gate 11 (lines 1503-1506) — but the verdict-engine zone (its own declared Wave-1 dependency) already deletes it, and that zone's `TreeGateResult` enrichment rewrites the same `validate_tree` range (1387-1507), so every helpers.py line number cited in this zone's spec will be stale by the time it executes.
   *Fix:* Drop the gate-11 instruction from this zone's spec; anchor all Wave-2+ targets to function/symbol names rather than pre-Wave-1 line numbers.

10. **Duplicated `helpers.py:1768` fix (OCR zone vs RTL/garble zone).** The OCR zone's helpers.py target and its regression test (`test_zone2_garble_check_expected_script.py`) instruct the identical `expected_script` fix on the `image_enrichment_promoted` garble check that the RTL/garble zone already makes in Wave 2. The Wave-3 fixer will find nothing to change and may (correctly) flag the spec as fabricated.
    *Fix:* Remove the 1768 target and its test from the OCR zone, or convert it to a verify-only assertion that the RTL/garble zone's fix is present.

11. **Wave-2 rationale understates real helpers.py overlap (reason-routing vs RTL/garble).** The rationale claims reason-routing zone's helpers.py touches are "light — importing already-landed types, not rewriting functions," but its own code_targets rewrite `classify_verdict` (1677-1727, 1743) and `_flat_text_is_garbled` (2892-2909) in helpers.py — while the RTL/garble zone concurrently rewrites `garble_prongs` and `validate_tree` in the same file in the same wave. Two parallel Wave-2 agents would both edit helpers.py under a rationale claiming only one does.
    *Fix:* Move reason-routing zone's helpers.py targets (classify_verdict typing, `_flat_text_is_garbled` typing) into the verdict-engine zone (Wave 1), leaving the reason-routing zone purely client.py + root scripts.

### Minor Issues

12. **Root-script wiring checks fall outside the stated `src/pageindex_mcp/` rule.** The verdict-engine zone's wiring checks list `promotion_sweep.py`/`preprocess_client.py` (repo-root scripts) as `must_be_imported_by` targets. They are legitimate production-code targets, but an automated gate keyed strictly to `src/pageindex_mcp/` would miss or reject them.
    *Fix:* Relax the wiring-gate rule to an explicit allowlist including the two root scripts, or note the exception in the spec.

13. **Line-number drift in the verdict-persistence zone's mechanism description.** `registry_backfill._enrich_one` is at line 187 (spec says 202) and `_heal_one` at line 333 (spec says 339). The wave rationale also names `worker.py` as a touched file though no code_target modifies it.
    *Fix:* Correct the line references; drop `worker.py` from the rationale or add a target for it.

14. **Test-file naming collisions across zones.** The RTL/garble zone (priority 2) writes `tests/test_zone3_*.py`; the OCR zone (priority 4) writes `tests/test_zone2_*.py`; the reason-routing zone's new tests share the `tests/test_zone5_*` namespace with the already-existing `tests/test_zone5_script.py`. Confusing at best, collision risk at worst.
    *Fix:* Rename test files to match each zone's actual identity (e.g., `test_zone_rtl_*.py`, `test_zone_ocr_*.py`) and check for existing filename collisions before assigning.

### Recommended Path to Approval

Before dispatching any wave to a fix agent:
1. Resolve blockers 3-6 by consolidating ownership: verdict-engine zone owns helpers.py's `classify_verdict`/`_flat_text_is_garbled`/thresholds/dead-gate/`HARD_FAIL_DEFECTS`; reason-routing zone owns client.py's `first_defect`/routing/`ExtractionSnapshot`/the single `decide_route`+`Route` definition. Neither zone should independently touch the other's owned files.
2. Move the verdict-persistence zone to Wave 3 (blocker 2) and fix its file paths (blocker 1); resolve its promotion_sweep.py/preprocess_client.py conflict with the verdict-engine zone in favor of the engine zone's ownership (blocker 3).
3. Replace the four vacuous/unverifiable wiring checks (issues 7-8) with real consumer-side or migration-existence assertions.
4. Drop the two duplicated code_targets (issues 9-10) and correct the Wave-2 rationale (issue 11).
5. Apply the two minor path/naming corrections (issues 12-14).

Once these are applied, re-run validation before execution.
