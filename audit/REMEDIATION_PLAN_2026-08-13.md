# Remediation Plan — 2026-08-13

**Audit:** audit/REMEDIATION_SCORECARD_2026-08-13_POST-FIX-2.md
**Zones:** 3 of 6 (top by priority)
**Waves:** 3

## Priority Scores

| Zone | Score | Severity | Bug Count | Proposal Status | Excluded |
|---|---|---|---|---|---|
| Verdict engine: 11-gate first-match cascade + a second engine that re-derives the same signals | 72 | critical | 12 | partially_implemented | no |
| Six Arabic/RTL order deciders + 10-prong garble gate via 13 differently-shaped call sites | 54 | critical | 9 | partially_implemented | no |
| Verdict persistence: five writers, no CAS, sidecar-only | 28.8 | high | 8 | no_proposal | no |
| Flag and threshold sprawl: ~35 kill-switches | 25.2 | high | 7 | no_proposal | no |
| OCR escalation vs per-picture enrichment: mutually-exclusive subsystems joined by a fragile marker-count contract | 13.2 | critical | 11 | implemented_and_wired | no |
| pdf_to_markdown_docling: dual candidate pipelines and stage ordering | 8.1 | high | 9 | implemented_and_wired | no |

Scoring formula: `severity_weight × bug_count × status_multiplier` (severity: critical=4, high=3; status: no_proposal=1.2, partially_implemented=1.5, implemented_and_wired=0.3).

This plan covers the top 3 zones by score (RTL/garble consolidation, verdict engine, verdict persistence). The two `no_proposal` zones (flag sprawl, verdict persistence — persistence has a partial spec here) and the two already-`implemented_and_wired` zones are deferred: the former need simplification proposals drafted before a fix cycle can target them, the latter are lower urgency since the known fix is already landed and remaining bugs are architectural residue, not stalled work.

## Wave Sequence

### Wave 1 — RTL/garble signal consolidation
**Zone:** Six Arabic/RTL order deciders + 10-prong garble gate via 13 differently-shaped call sites

**Rationale:** Consolidates the signal producers (`garble_prongs`, `_is_garbled_blob`, `_check_bidi_coherence`, `_tree_is_rtl_reversed`, `_detect_arabic_reversal`, `reconstruct_bidi_order`) down to `decide_rtl()` and makes `expected_script` keyword-required. These are the inputs Wave 2's gate table consumes, so they must stabilize first.

**Primary files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/converters.py`

**Shared-file conflicts:** None with prior waves (first wave). Wave 2 depends on the shapes this wave leaves in `helpers.py`/`converters.py` — see the note under Wave 2's fix spec on `_rtl_decision` caching.

### Wave 2 — Verdict engine consolidation
**Zone:** Verdict engine: 11-gate first-match cascade + a second engine that re-derives the same signals

**Rationale:** Replaces `validate_tree`'s sequential early-return gates with a declarative gate table and deletes `classify_verdict`'s re-derivation path. Must follow Wave 1 because the gate table calls the garble/RTL functions Wave 1 just consolidated (in particular the RTL_REVERSAL and BIDI_DEGRADED gates must wrap the single cached `decide_rtl` decision Wave 1 produces, not reintroduce independent calls). Must precede Wave 3 because Wave 3's persistence writers consume the `TreeGateResult`/`classify_verdict` contract this wave redefines.

**Shared-file conflicts:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/converters.py` — same files as Wave 1, so this wave cannot run in parallel with Wave 1; it must execute strictly after.

### Wave 3 — Verdict persistence consolidation
**Zone:** Verdict persistence: five writers, no CAS, sidecar-only

**Rationale:** Consolidates the five verdict writers and closes the sidecar/artifact-sync gap. Its writers (`worker.py`, `storage.py`, `registry_backfill.py`, `promotion_sweep.py`, `preprocess_client.py`) call `classify_verdict` and consume `TreeGateResult`, both redefined in Wave 2. No file overlap with Waves 1–2 (primary files are `worker.py`, `storage.py`, `registry_backfill.py`, plus the two top-level scripts), but the type/contract dependency on Wave 2 forces sequencing after it.

**Shared-file conflicts:** None.

## Fix Specs

### Zone: Six Arabic/RTL order deciders + 10-prong garble gate via 13 differently-shaped call sites (wave 1, priority 2)

**Mechanism to eliminate:** Six independent RTL/bidi reversal deciders (`_detect_arabic_reversal`, `_text_is_logical_order`, `_heading_is_logical_order`, `reconstruct_bidi_order`, `_fix_residual_rtl_reversal` in `converters.py`; `_tree_is_rtl_reversed`, `_check_bidi_coherence` in `helpers.py`) each with different sampling strategies and Arabic-ratio thresholds, producing contradictory verdicts on the same text. `garble_prongs`/`_is_garbled_blob` accepts `expected_script` as optional (default `None`), letting call sites omit it and unconditionally disable the `latin_gibberish` prong. No shared RTL decision flows from extraction through validation — extraction can decide "already logical" while the gate later decides "reversed," and a successful bidi repair removes the presentation-form signature the garble gate keys on, so a correct repair erases its own escalation trigger.

**Strategy:** Delete the five legacy converter/helper RTL deciders (thin shims delegating to `decide_rtl` but adding indirection and redundant re-invocation). Consolidate to `decide_rtl` as the sole RTL decider, replacing `_detect_arabic_reversal`'s vocab-list method. Delete `_fix_residual_rtl_reversal` (redundant with `reconstruct_bidi_order`, which already delegates to `decide_rtl`+`apply_rtl`). Collapse `_tree_is_rtl_reversed` and `_check_bidi_coherence` into a single cached `decide_rtl` call in `validate_tree`. Make `expected_script` a required keyword on `_is_garbled_blob`, fixing the hole at `helpers.py:1942`.

**Note on the "thread RTL decision from extraction into validation" claim:** the audit's suggested strategy sentence has no corresponding code target in this spec — no target plumbs the extractor's `RtlDecision` into `validate_tree`/`classify_verdict`. Treat this as an unimplemented stretch goal for a future zone, not a Wave-1 deliverable; do not block Wave 1 completion on it.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `converters.py` | 111-132 | Delete `_detect_arabic_reversal` | Replace its call site in `_inject_arabic_structural_headings` (line 167, and 173/183) with `decide_rtl(md).reversed` | Must still detect Tesseract mirror-reversed Arabic OCR output and match headings against forward-oriented regexes |
| `converters.py` | 1459-1467 | Delete `_text_is_logical_order` | **Not zero-callers** — verified imported by `tests/test_rfc027_d3.py` and `tests/test_rfc010_converters.py`. Delete the production function; port the behavioral cases in those tests to `decide_rtl` equivalents (see Test disposition below) before deleting the shim | Any remaining production caller must be inlined with a direct `decide_rtl` call first |
| `converters.py` | 1470-1480 | Delete `_heading_is_logical_order` | Sole production caller is `reconstruct_bidi_order` (line 1512); inline `decide_rtl(heading_text.strip(), sample_count=1).reversed` directly into its per-heading loop | Per-heading correction must still run even when the document-level decision is not-reversed |
| `converters.py` | 1527-1538 | Delete `_fix_residual_rtl_reversal` | **Not zero test-callers** — verified imported by `tests/test_rfc010_converters.py` and `tests/test_zone5_order_verdict_wrappers.py`. Remove the redundant call at line 2543 in `_pre_inference_normalize`, then delete the function; retire/rewrite the two test files' equivalence classes for this shim | `_pre_inference_normalize` must still produce correctly-ordered bidi output via `reconstruct_bidi_order` alone |
| `converters.py` | 2542-2543 | Remove redundant `_fix_residual_rtl_reversal` call from `_pre_inference_normalize` | Delete the second-pass call; `reconstruct_bidi_order` already covers the same `decide_rtl`+`apply_rtl` logic | `reconstruct_bidi_order` remains the single bidi normalization step |
| `helpers.py` | 1485-1499 | Delete `_tree_is_rtl_reversed` | Sole caller is `validate_tree` (line 1557). Compute `_rtl_decision = decide_rtl(sig.flat_text) if sig.flat_text else None` once, reuse for both this and the bidi gate | `validate_tree` must still return `TreeGateResult(False, TreeDefect.RTL_REVERSAL, signals=sig)` when reversed; reuse `sig.flat_text` rather than re-flattening |
| `helpers.py` | 1344-1358 | Delete `_check_bidi_coherence` | Sole caller is `validate_tree` (line 1573). Fold into the same cached `_rtl_decision`; if reversed, RTL_REVERSAL wins (higher priority); otherwise branch on morphology evidence for BIDI_DEGRADED | `BIDI_COHERENCE_ENFORCE` env-var gating (lines 1575-1586) must be preserved; BIDI_DEGRADED remains verdict-only, not persistence-gating |
| `helpers.py` | 1298-1304 | Make `expected_script` a required keyword on `_is_garbled_blob` | Change signature to `def _is_garbled_blob(blob: str, *, expected_script: str | None, blob_kind: BlobKind = BlobKind.TREE_TEXT) -> bool` | Existing callers passing `expected_script=` keep working; only the omitting site fails, and must be fixed |
| `helpers.py` | 1942 | Fix the `_is_garbled_blob` call in `classify_verdict`'s `image_enrichment_promoted` path that omits `expected_script` | Change to `_is_garbled_blob(_promoted_text, expected_script=expected_script)` | This is the **only** owner of this edit — do not duplicate it in Wave 2 (see Validation Results below); `image_enrichment_promoted` must now correctly reject latin-gibberish Arabic docs (وارد 597 class) |
| `helpers.py` | 1209-1212 | Verify all `_is_garbled_blob` callers pass `expected_script` | `garble_prongs` signature unchanged; extend the existing test to scan `_is_garbled_blob` call sites too | `garble_prongs` signature must not change |
| `client.py` | 1394-1412 | Verify retry-comparison `_is_garbled_blob` calls already pass `expected_script` | No code change; must continue compiling once the keyword becomes required | Retry-wins comparison logic must not change |
| `converters.py` | 1706-1709 | Verify `_text_layer_has_content`'s `_is_garbled_blob` call passes `expected_script` | No code change needed | Text-layer garble check must keep working for Arabic and Latin |
| `converters.py` | 2205-2212 | Verify picture-OCR `_is_garbled_blob` call passes `expected_script` | No code change needed | Region-level garble check for picture OCR must keep working |

**Test disposition (added per validation finding — see Validation Results):**
- `tests/test_rfc010_converters.py`, `tests/test_rfc027_d3.py` — port `_text_is_logical_order` cases to direct `decide_rtl` assertions.
- `tests/test_rfc010_converters.py`, `tests/test_zone5_order_verdict_wrappers.py` — port `_fix_residual_rtl_reversal` cases to `reconstruct_bidi_order` idempotence assertions.
- `tests/test_converters.py`, `tests/test_rfc036_d5_arabic_heading_fixes.py`, `tests/test_zone5_order_verdict_wrappers.py` — port `_detect_arabic_reversal` cases to `decide_rtl` assertions in `_inject_arabic_structural_headings` context.
- New: `tests/test_zone3_expected_script_required.py`, `tests/test_zone3_rtl_consolidation.py`, `tests/test_zone3_garble_script_hole.py`, extend `tests/test_zone3_rtl_decision.py`.

**Wiring checks:**

| Symbol | Must be imported/called by | Check type |
|---|---|---|
| `decide_rtl` | `converters.py`, `helpers.py`, `client.py` | call |
| `_is_garbled_blob` (required `expected_script`) | `helpers.py`, `converters.py`, `client.py` | call |
| `BlobKind` | `helpers.py`, `converters.py` | import |
| `normalize_for_garble` | `helpers.py` | call |
| `GARBLE_DIGIT_FLOOR` | `helpers.py` | import |
| `reconstruct_bidi_order` | `converters.py`, `client.py` | call |

**Test requirements:**
- Scan all `_is_garbled_blob` call sites (not just `garble_prongs`) for explicit `expected_script=`; calling without it raises `TypeError`.
- Confirm `_detect_arabic_reversal`, `_text_is_logical_order`, `_heading_is_logical_order`, `_fix_residual_rtl_reversal`, `_tree_is_rtl_reversed`, `_check_bidi_coherence` are all deleted (import raises). `decide_rtl` is the sole RTL decision point. `_inject_arabic_structural_headings` still detects reversed OCR via `decide_rtl`.
- `_pre_inference_normalize` calls `reconstruct_bidi_order` exactly once; idempotence holds across two applications.
- `validate_tree` computes the RTL decision exactly once (single `decide_rtl` call on `flat_text`) used for both RTL_REVERSAL and BIDI_DEGRADED gates; `BIDI_COHERENCE_ENFORCE` still gates BIDI_DEGRADED.
- Regression: >60% Latin-gibberish blob with `expected_script='Arab'` → `_is_garbled_blob` returns `True`; `classify_verdict`'s `image_enrichment_promoted` path now rejects it instead of PASS (وارد 597 class).
- `garble_prongs` with `BlobKind.RAW_MARKDOWN` strips heading/pipe characters before ratio computation; `BlobKind.TREE_TEXT` does not.
- `reconstruct_bidi_order` applies the same decision to headings and body; a doc just under the 0.15 Arabic-ratio threshold is skipped consistently for both.

**Corpus validation:** وارد 597, سياسة حوكمة, UN Human Rights doc, Federal Decree-Law 13/2022, Federal Decree-Law No.47 — expected direction: improve (verdicts tighten from stale PASS). Spot-check count: 5.

**Estimated complexity:** large. **Severity:** critical.

---

### Zone: Verdict engine: 11-gate first-match cascade + a second engine that re-derives the same signals (wave 2, priority 1)

*Note on naming: the current `validate_tree` has 10 gates, not 11 — gate 11 (`arabic_low_content_ratio`) was already removed per the comment at `helpers.py:1630-1635`. Treat "11-gate" in the zone name as legacy naming; the operative gate count is 10 (verified: `TreeDefect` has exactly 10 non-OK, non-deprecated members, matching the `GATE_TABLE`-length test below).*

*Note on staleness: the mechanism text below claims `promotion_sweep.py:99` and `preprocess_client.py:306` exercise the legacy string-acceptance path. Verified against current source: both already pass a `TreeGateResult` (`promotion_sweep.py` reconstructs one via `_defect_from_reason_str` at lines 96-98; `preprocess_client.py` passes `validate_tree`'s result at 305-306). The str-branch deletion below is still valid — it is dead in production but kept alive by tests that pass bare strings/None — so treat this as a hardening step, not a behavior fix for those two call sites.*

**Mechanism to eliminate:** `validate_tree` (`helpers.py:1502-1636`) evaluates gates as sequential early-return statements: the first failing gate wins and co-firing defects are masked. `classify_verdict` (`helpers.py:1835-2018`) accepts either a `TreeGateResult` or a legacy reason string; on the legacy-string path it re-derives `TreeSignals` from scratch via `TreeSignals.from_tree` (line 1893), a second independent signal computation with no consistency check against `validate_tree`. Even on the `TreeGateResult` path, `classify_verdict` unconditionally re-computes `_tree_node_count`/`_flatten_tree_text` (line 1874) before checking whether signals were already attached. `classify_verdict` at line 1900 independently checks `sig.is_reordered` as a fallback hard-fail, which can disagree with `validate_tree`'s defect enum.

**Strategy:** Three-phase consolidation: (1) replace the sequential early-return gates in `validate_tree` with a declarative gate table evaluated exhaustively, returning a `TreeGateResult` carrying a `frozenset[TreeDefect]` of all co-firing defects plus the primary (highest-severity) defect; (2) delete the legacy string-acceptance path in `classify_verdict` by requiring `TreeGateResult | None`; (3) fix the zero-content fast path to reuse signals already attached to `TreeGateResult`.

**Cross-wave reconciliation (per validation finding):** all line references below are pre-Wave-1. By the time this wave runs, Wave 1 has already deleted `_tree_is_rtl_reversed`/`_check_bidi_coherence` and inlined a single cached `_rtl_decision = decide_rtl(sig.flat_text)` into `validate_tree`. The RTL_REVERSAL and BIDI_DEGRADED entries in `GATE_TABLE` must **wrap that same cached decision** — compute `decide_rtl` once outside the table loop (as Wave 1 left it) and pass the decision into both gate-check functions, rather than each gate independently re-calling `decide_rtl`. Do not re-derive RTL/bidi logic from the pre-Wave-1 shims described historically in the audit.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers.py` | 83-102 | Add `all_defects` field to `TreeGateResult` | Add `all_defects: frozenset[TreeDefect] = frozenset()`; primary `defect` field stays for backward compat; `__iter__` keeps yielding `(ok, reason_str)` | Must remain iterable as `(ok, reason_str)` at all existing call sites incl. `client.py:1249` |
| `helpers.py` | 199-212 | Define `GATE_TABLE`: declarative list of `(check_fn, TreeDefect)` pairs | Extract each gate's logic from the current `validate_tree` body into standalone functions taking `(sig, structure, expected_script, page_count)`; table order defines primary-defect severity priority (garbling first, suspect_density last), but **all** entries are always evaluated. RTL/bidi gate functions take the pre-computed `_rtl_decision` (see reconciliation note above) rather than calling `decide_rtl` themselves | Gate evaluation order must match current priority order for primary-defect selection; gate semantics byte-identical to current behavior for single-defect docs |
| `helpers.py` | 1502-1636 | Rewrite `validate_tree` to iterate `GATE_TABLE` exhaustively | Compute `TreeSignals` once, iterate `GATE_TABLE`, collect all firing `(TreeDefect, detail)` pairs, return `TreeGateResult(ok=len(fired)==0, defect=fired[0].defect if fired else OK, all_defects=frozenset(...))` | Still returns `TreeGateResult` iterable as `(ok, reason_str)`; `BIDI_COHERENCE_ENFORCE` gating preserved inside the bidi gate function |
| `helpers.py` | 1873-1893 | Eliminate zero-content fast-path re-computation in `classify_verdict` | Move the zero-content check after the normalize block so `sig` (from the passed `TreeGateResult`) is available; check `sig.node_count == 0 or len(sig.flat_text.strip()) == 0` | `zero_content` FAIL must still fire for empty trees; `test_zone1_classify_verdict.py` parity tests must pass |
| `helpers.py` | 1838, 1856-1861, 1877-1893 | Delete the legacy string-acceptance path in `classify_verdict` | Remove the `elif isinstance(validate_result, str)` and `else` branches; signature becomes `validate_result: TreeGateResult | None`; `None` path (flat docs from `client.py:2004`) stays supported | All callers updated before deletion; `client.py:2004`'s `None` path keeps working |
| `helpers.py` | 1820-1832 | Keep `_defect_from_reason_str`, internal-only | Still required by `promotion_sweep.py` to reconstruct `TreeGateResult` from stored `verdict_reason` strings; drop the import from `client.py:60` only if unused there | `promotion_sweep.py:96` still needs this function |
| `helpers.py` | 1900-1901 | Remove the redundant `sig.is_reordered` independent fallback in `classify_verdict` GROUP 1 | Delete the standalone `if sig.is_reordered: return 'FAIL', 'reordered'` — with the exhaustive gate table, `TreeDefect.REORDERED` is already caught via `all_defects`/`HARD_FAIL_DEFECTS` | Rewrite `test_zone1_classify_verdict.py::TestIsReorderedDualDerivation`'s artificial-scenario test to verify the exhaustive table catches reordering instead |
| `promotion_sweep.py` | 94-101 | Verify `TreeGateResult` (not a string) always reaches `classify_verdict` | Already correct — no change; confirm at review time | Stored `verdict_reason` strings in MinIO `meta.json` must remain parseable via `_defect_from_reason_str` |
| `preprocess_client.py` | 290-307 | Ensure `classify_verdict` receives `TreeGateResult`, not string | The `validate_tree`-driven path (line 306) is already correct. The sidecar-only path (lines 290-298) currently skips `classify_verdict` entirely for docs with existing verdicts — change it to construct a `TreeGateResult` from the stored defect and re-run `classify_verdict`, ensuring stored verdicts stay consistent with current gate logic | `preprocess_client.py` is top-level, uses absolute `from pageindex_mcp.helpers import ...`; re-running `classify_verdict` on the sidecar path is an intentional behavior change |

**Explicitly removed from this spec (validation finding — duplicate edit):** the original spec's code target instructing a change to `helpers.py:1942` (`_is_garbled_blob(_promoted_text)` → keyword form) is **dropped from this zone**. Wave 1 already owns and lands that exact edit, and by Wave 2 the `expected_script` keyword is already required there — attempting the same string-replace in Wave 2 will fail (`old_string` no longer exists) or double-apply. This zone only needs a **test** asserting the call passes `expected_script` (already covered by Wave 1's `test_zone3_garble_script_hole.py`); no code target needed here.

**Wiring checks:**

| Symbol | Must be imported/called by | Check type |
|---|---|---|
| `GATE_TABLE` | consumed only within `helpers.py` (defining module) — verify via call-site count, not cross-module import | call |
| `TreeGateResult.all_defects` | `helpers.py` (internal use); **add** a genuine consumer in `client.py` — e.g. log co-firing defects into the extraction snapshot / verdict provenance emitted around `client.py:1249` — since no code target otherwise wires `all_defects` into `client.py` and the original wiring check would fail by construction without one | call |
| `classify_verdict` (`TreeGateResult`-only signature) | `client.py`, `promotion_sweep.py`, `preprocess_client.py` | call |
| `_defect_from_reason_str` | `promotion_sweep.py`, `preprocess_client.py` | import |

**Test requirements:**
- Exhaustiveness: a tree simultaneously garbled + node_count<3 + reordered → `all_defects` contains all three `TreeDefect` values; primary defect is GARBLING (highest priority). Core anti-masking test.
- Contract: each `GATE_TABLE` gate fires independently with the correct `TreeDefect`, parameterized over all 10 gates.
- Exhaustiveness: `len(GATE_TABLE) == len([d for d in TreeDefect if d not in (OK, ARABIC_LOW_CONTENT_RATIO)])` — prevents a new `TreeDefect` shipping without a gate.
- Contract: `classify_verdict` rejects a bare string with `TypeError`; existing string-compat tests rewritten to construct `TreeGateResult`.
- Regression: zero-content fast path uses `sig` from `TreeGateResult` — mock `_tree_node_count`/`_flatten_tree_text` to raise if called.
- Regression: `image_enrichment_promoted` garble check (owned by Wave 1) — assert via this zone's tests that `classify_verdict` correctly rejects Latin-gibberish-in-Arabic blobs, without re-editing the call site.
- Regression: ward-597-class masking bug — node_count<3 AND garbled reports both defects in `all_defects`, not just `node_count_low` (obs #5330).
- Contract: `sig.is_reordered` independent fallback removed; `TreeGateResult(defect=REORDERED, all_defects⊇{REORDERED})` reaches FAIL/reordered via `HARD_FAIL_DEFECTS`, not a separate check.

**Corpus validation:** ward_597, siyasat_hawkama, ghv_tkv_tarif, reitlehrer, federal_decree_law_13_2022, un_human_rights, cabinet_resolution_no_21 — expected direction: improve. Spot-check count: 7.

**Estimated complexity:** large. **Severity:** critical.

---

### Zone: Verdict persistence: five writers, no CAS, sidecar-only (wave 3, priority 3)

*Note on staleness (validation finding): the mechanism text below claims "no CAS" and "no shared entry point." Verified against current source: `storage.py` already contains `_verdict_cas_guard` (line 515, invoked from `save_doc_meta` at 610) and `write_verdict` (line 644), and `client.py` already imports/calls `write_verdict` (lines 102, 2159-2163). The real remaining gaps are narrower: `promotion_sweep.py`/`preprocess_client.py` still bypass `write_verdict` for verdict fields, and the registry's `verdict != 'FAIL'` filter admits empty-string verdicts. Read the mechanism below as "the machinery exists but two writers still bypass it," not "the machinery doesn't exist."*

**Mechanism to eliminate:** `promotion_sweep.py` and `preprocess_client.py` recompute verdicts with fewer inputs than ingest had, then write only the sidecar via `save_doc_meta` — leaving the artifact's verdict stale and disagreeing with the sidecar. `registry_backfill.py` copies verdict from whichever source it reads first and re-writes, risking clobbering a newer verdict with an older one (mitigated by the existing CAS guard, but not by writer discipline). The `WHERE verdict != 'FAIL'` SQL filter passes docs with `verdict=''` (empty string from fresh upserts before reconcile), making FAIL-equivalent tree docs queryable. The three-writer job-status hash (upload_app pending, worker processing/done/error, reaper error) has no validated state machine.

**Strategy:** Consolidate all verdict *mutation* through `write_verdict` as the single entry point — `promotion_sweep.py` and `preprocess_client.py` must call it instead of `save_doc_meta` for verdict fields, keeping artifact and sidecar in sync. `registry_backfill.py` must never write verdict fields itself — it only copies them from the authoritative source (artifact first, sidecar fallback), never recomputing. Fix the SQL filter to exclude empty-string verdicts. Introduce a `JobStatus` enum with validated transitions to replace the three-writer string-status hash, defined in a module both `worker.py` and `upload_app.py` can import without a dependency cycle.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `promotion_sweep.py` | 99-121 | Route verdict fields through `write_verdict`, non-verdict fields through `save_doc_meta` | After `classify_verdict` (line 99), call `write_verdict(doc_id, verdict, verdict_reason, CURRENT_PIPELINE_VERSION, verdict_computed_at, mlr, content_class=content_class)`; then `save_doc_meta` with only non-verdict provenance (doc_id, doc_name, source_url, processed_at, content_class) | Must not change `sweep_candidates` query or `classify_verdict` call signature; preserve the `_defect_from_reason_str → TreeGateResult` reconstruction at 91-98 |
| `preprocess_client.py` | 309-322 | Route verdict fields through `write_verdict` | After verdict computation (289-307), call `write_verdict(did, verdict, verdict_reason, CURRENT_PIPELINE_VERSION, verdict_computed_at, mlr, content_class=content_class)`; `save_doc_meta` for non-verdict provenance only. Import `write_verdict` from `pageindex_mcp.storage` at line 233 | Must not change the `is_flat` branch (289-299) or the `validate_tree` re-run (305); flat-doc path must still reuse stored verdict/verdict_reason/max_leaf_ratio verbatim |
| `registry_backfill.py` | 187-204 | `_enrich_one`: verdict fields pass through unmutated | No functional change — `read_registry_fields` already carries verdict fields (artifact or sidecar fallback), and `save_doc_meta`'s CAS guard protects against clobbering. Add a debug log confirming passthrough | Must not introduce a `write_verdict` call here — backfill copies, it does not compute |
| `registry_backfill.py` | 333-358 | `_heal_one`: verdict fields pass through unmutated | No code change — existing sidecar-fallback merge (339-357) is safe under CAS. Add a comment documenting `_heal_one` as a propagator, never a computer of verdicts | Must never call `classify_verdict` |
| `registry.py` | 322-327, 405, 417, 537 | Fix verdict filter to exclude empty strings | Change `verdict != 'FAIL'` to `verdict NOT IN ('FAIL', '')` in `_LIST_SQL`, `_COUNT_SQL`, and `faceted_search` | Must not change `_COUNT_ALL_SQL` (deliberately unfiltered) or `sweep_candidates` (filters by pipeline_version, not verdict) |
| `storage.py` | 644-728 | Document `write_verdict` as the sole verdict-mutation entry point | `pipeline_version` param already exists (line 648) — no signature change; add docstring stating callers must never mutate verdict fields via `save_doc_meta` directly | Must not change `save_doc_meta`'s read-merge-write behavior or remove `_verdict_cas_guard` |
| `worker.py` | 446-553 | Replace string-literal job-status transitions with a validated `JobStatus` state machine | Define `JobStatus(StrEnum)` = PENDING/PROCESSING/DONE/ERROR with values equal to current string literals; `_VALID_TRANSITIONS` = {PENDING→{PROCESSING}, PROCESSING→{DONE,ERROR}, DONE→{}, ERROR→{}}; `_set_job_status(redis, job_id, new_status, **fields)` validates before writing. Apply at 449, 553, 468-498, 580-589, and reap_stale_jobs:650 | Must not change `JOB_TTL`/`REAP_GRACE`; Redis hash `status` field values stay identical strings for backward-compat polling |
| **new module** (e.g. `job_status.py`, shared by worker.py and upload_app.py) | — | Define `JobStatus`/`_VALID_TRANSITIONS`/`_set_job_status` in a location both can import | **Per validation finding:** do NOT have `upload_app.py` import from `worker.py` — that would pull the arq worker module (which imports client/converters) into the lightweight upload HTTP app, violating the no-dependency-cycle constraint. Define the enum/transitions/setter in a small shared module instead | Both `worker.py` and `upload_app.py` import from the shared module, not from each other |
| `upload_app.py` | 174-177 | Use the shared `_set_job_status` for the initial PENDING write | Import `JobStatus`/`_set_job_status` from the new shared module; replace the raw dict write with a validated PENDING write | Must not change the HTTP 202 response or `/status/{job_id}` GET endpoint |

**Wiring checks:**

| Symbol | Must be imported/called by | Check type |
|---|---|---|
| `write_verdict` | `client.py`, `promotion_sweep.py`, `preprocess_client.py` | call |
| `JobStatus` | `worker.py`, `upload_app.py` (via shared module, not worker→upload_app or vice versa) | dispatch |
| `_set_job_status` | `worker.py`, **and `upload_app.py`** (per validation finding — the original check omitted `upload_app.py`, which would let it keep writing raw dicts undetected) | call |
| `_VALID_TRANSITIONS` | consumed only within the shared status module (defining module) — verify via call-site count, not cross-module import | call |

**Note on wiring-check tooling (validation finding):** `promotion_sweep.py` and `preprocess_client.py` are top-level scripts outside `src/pageindex_mcp/`. Confirm the wiring-verification gate scans repo-root scripts; if it only scans `src/`, add explicit grep-based test assertions for the `write_verdict` call sites in these two files instead of relying on the wiring gate.

**Test requirements:**
- Contract: `promotion_sweep` calls `write_verdict` (not `save_doc_meta`) for verdict fields, updating artifact and sidecar atomically.
- Contract: `preprocess_client.recompute_verdicts` calls `write_verdict` with correct `(verdict, verdict_reason, pipeline_version, verdict_computed_at, max_leaf_ratio)` args (mocked).
- Contract: `registry_backfill._enrich_one`/`_heal_one` never call `classify_verdict` or `write_verdict` — propagation only.
- Regression: SQL verdict filter excludes both `'FAIL'` and `''`; `list_docs`/`count_docs`/`faceted_search` return 0 rows for verdict-only-empty or verdict-only-FAIL fixtures.
- Contract: `write_verdict` is the sole verdict-mutation entry point — a `save_doc_meta` call carrying verdict fields but no `verdict_computed_at` is rejected by the CAS guard when an existing sidecar has a newer timestamp.
- Exhaustiveness: every status string used in `worker.py`, `upload_app.py`, `reap_stale_jobs` has a corresponding `JobStatus` member.
- Contract: invalid transitions (PENDING→DONE, DONE→PROCESSING, ERROR→PROCESSING) rejected by `_set_job_status`.
- Contract: valid transitions (PENDING→PROCESSING, PROCESSING→DONE, PROCESSING→ERROR) succeed.
- Regression: concurrent `write_verdict` calls with different timestamps — newer `verdict_computed_at` wins regardless of execution order (temporal CAS integration).

**Corpus validation:** all 25 corpus documents (any doc previously recomputed by `promotion_sweep`/`preprocess_client` may now have its artifact updated instead of sidecar-only); docs with `verdict=''` in Postgres registry will be newly excluded from query results. Expected direction: stable. Spot-check count: 5.

**Estimated complexity:** large. **Severity:** high.

## Validation Results

**Overall quality:** adequate. **Approved:** false — the following issues were found during validation of the input spec and are incorporated as corrections into the Fix Specs above rather than left as open defects:

**Major issues (resolved above):**
1. **Duplicate edit target across waves** — original Zone-1 code target 7 (`helpers.py:1942`) duplicated Zone-3/Wave-1's identical edit at the same line, which would also already be a required-keyword call by Wave 2. **Resolved:** removed the code target from the verdict-engine spec; Wave 1 solely owns that edit, Wave 2 only asserts it via tests.
2. **Vacuous/incomplete wiring check** — `TreeGateResult.all_defects` was required to be called by `client.py` with no code target wiring it there. **Resolved:** added a code target requiring a genuine `all_defects` consumer in `client.py` (logging co-firing defects into verdict provenance).
3. **Stale line references across waves** — Zone-1's line numbers describe the pre-Wave-1 file; Wave 1 restructures the same `validate_tree` RTL/bidi region into a single cached `decide_rtl` call, which Zone-1's per-gate check-function shape didn't originally reference. **Resolved:** added an explicit reconciliation note requiring `GATE_TABLE`'s RTL_REVERSAL/BIDI_DEGRADED entries to wrap Wave 1's cached `_rtl_decision`.
4. **False "zero callers" claim** — `_text_is_logical_order`, `_fix_residual_rtl_reversal`, and `_detect_arabic_reversal` are imported by six existing test files not mentioned in the original spec, which would leave Wave 1 landing with a red test suite. **Resolved:** added explicit test-disposition instructions (port behavioral cases, then delete/rewrite the six affected test files) to the Zone-3/Wave-1 spec.

**Minor issues (resolved above):**
5. Stale mechanism claim about `promotion_sweep.py:99`/`preprocess_client.py:306` exercising the legacy string path — both already pass `TreeGateResult`. Reframed as a hardening step in the Wave-2 spec.
6. Verdict-persistence mechanism overstated "no CAS"/"no shared entry point" — `_verdict_cas_guard` and `write_verdict` already exist and are already called from `client.py`. Reframed in the Wave-3 spec as "machinery exists, two writers bypass it."
7. `_set_job_status` wiring check omitted `upload_app.py`, and routing `upload_app.py`'s `JobStatus` import through `worker.py` would violate the no-dependency-cycle constraint. **Resolved:** spec now defines `JobStatus`/`_VALID_TRANSITIONS`/`_set_job_status` in a new shared module imported by both, and the wiring check now includes `upload_app.py`.
8. Self-referential (vacuous) wiring checks for `GATE_TABLE` and `_VALID_TRANSITIONS`, which are only consumed in their own defining module. **Resolved:** annotated these as call-site-count checks rather than cross-module import checks.
9. Unimplemented strategy claim in Zone 3 ("thread the RTL decision from extraction into validation") has no code target. **Resolved:** flagged explicitly as an unimplemented stretch goal, not a Wave-1 deliverable.
10. Wiring checks reference top-level scripts (`promotion_sweep.py`, `preprocess_client.py`) outside `src/pageindex_mcp/`, which the wiring-verification gate may not scan. **Resolved:** flagged with a fallback instruction to use grep-based test assertions if the gate is `src/`-scoped only.
11. Zone name "11-gate first-match cascade" is stale — gate 11 was already removed; the operative count is 10. **Resolved:** noted at the top of the Wave-2 spec; `GATE_TABLE`-length exhaustiveness test uses the verified count of 10.

No blockers remain that would prevent starting Wave 1. The two deferred `no_proposal` zones (flag/threshold sprawl, and the persistence zone's original "no proposal" framing before this plan's Wave-3 spec was drafted) and the two `implemented_and_wired` zones (OCR/picture-enrichment, pdf_to_markdown_docling dual pipelines) are out of scope for this plan and should get dedicated simplification proposals before their own fix cycles.
