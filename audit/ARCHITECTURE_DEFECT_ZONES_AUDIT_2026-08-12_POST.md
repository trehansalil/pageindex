# Architecture Defect Zones Audit — 2026-08-12 POST

**Date:** 2026-08-12
**Sources:** 17 history miners, 1 code maps

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|------|----------|-----------|-----------|
| 1 | Tree/Flat Verdict Split | critical | 18 | `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/client/indexer.py` |
| 2 | Garble Detection Fragmentation | critical | 16 | `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/helpers/tree_validation.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/helpers/verdict.py` |
| 3 | Converter-Gate-Route Ordering Chain | critical | 14 | `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/helpers/types.py`, `src/pageindex_mcp/converters/pipeline.py`, `src/pageindex_mcp/worker/errors.py` |
| 4 | Registry Dual-Write Consistency | high | 8 | `src/pageindex_mcp/worker/registry_mirror.py`, `src/pageindex_mcp/storage/documents.py`, `src/pageindex_mcp/storage/verdict.py`, `src/pageindex_mcp/registry_backfill/reconcile.py` |
| 5 | Worker-Child Process Boundary | high | 5 | `src/pageindex_mcp/worker/job.py`, `src/pageindex_mcp/worker/errors.py`, `src/pageindex_mcp/worker/subprocess_mgr.py` |
| 6 | ZDR/PII Egress Gap | high | 3 | `src/pageindex_mcp/client/llm.py`, `src/pageindex_mcp/converters/pictures.py`, `src/pageindex_mcp/config.py`, `src/pageindex_mcp/server.py` |
| 7 | Arabic/RTL Pipeline Blindness | high | 14 | `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/converters/pipeline.py`, `src/pageindex_mcp/helpers/flat.py`, `src/pageindex_mcp/helpers/tree_validation.py` |
| 8 | Duplicated Convergent Logic | medium | 6 | `src/pageindex_mcp/helpers/flat.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/client/recovery.py`, `src/pageindex_mcp/client/images.py` |

## Zone Details

### Zone 1: Tree/Flat Verdict Split

**Severity:** critical | **Bug count:** 18

The tree-path and flat-path persistence methods (`_persist_tree_result` vs `_persist_flat_result`) compute document verdicts through fundamentally different gate evaluation pipelines, but both write to the same verdict sidecar and registry. The tree path threads the full `TreeGateResult` (10 gates) from `validate_tree` into `compute_verdict`, while the flat path discards the original gate result entirely and recomputes from scratch using only 3-of-10 gates (`FLAT_GATE_SUBSET`: garbling, node_garbling, reordered). Both paths then independently run a near-identical ~15-line verdict-ledger hysteresis block that can override the just-computed verdict. This split makes every gate/threshold fix a two-site change where the two sites have different normalization, different gate coverage, and different promotion logic.

#### Mechanism
Any fix to a gate, threshold, or promotion branch must be implemented twice (tree and flat) with different code paths that share no common validation contract. A fix applied to only one path (which is the default since they are in separate methods) creates an asymmetry that later manifests as a document oscillating between verdicts depending on which routing path it takes on each ingestion. The flat path's 3-gate subset also means that the 7 hard-fail gates designed to catch structural defects (empty_node_contamination, low_content_density, suspect_density, etc.) can never fire on flat content -- so a scanned PDF that gets no OCR (thin tree -> flat routing) lands in the one persistence path where the content-quality gates are structurally inert. Promotion branches in `apply_promotions` (image_enrichment_promoted, cat_b_promoted, small_doc_promoted) can each independently return PASS without re-consulting the full defect set beyond the first branch's `_structural_ok` check, so a non-hard-fail defect is silently promoted away. Each time a new promotion path is added or an existing one is tightened, it interacts with every other promotion branch and with the tree/flat gate split to produce emergent verdict flaps.

#### History
a. RFC-022 B1: synthetic structure from empty flat blocks triggered false garble (D3) and false PASS via cat_b_promoted (D4).
b. RFC-022 B2-B: reordered image_enrichment_promoted above max_leaf_ratio hard-fail.
c. RFC-023 D4: added content-quality guards to cat_b_promoted.
d. RFC-023 D5: expanded synthetic-structure guard.
e. RFC-024 D0: widened PASS_MAX_LEAF_RATIO from 0.20->0.30 causing garbled Haftpflicht to flip FAIL->PASS (3 test recalibrations).
f. RFC-025 D0: added hysteresis via find_prior_verdict, structurally dead on reingestion.
g. RFC-029 D1/D2: 4 new validate_tree reasons with no flat-path recovery routing -> 3 PASS->ERROR regressions.
h. RFC-030 D3: low_content_density threshold too aggressive for legal documents.
i. Run 9: image_enrichment_promoted let 38-123 char docs PASS (violating HR5).
j. Run 8->9: German garbled doc (Haftpflicht) FAIL->PASS via expected_script propagation gaps + hysteresis relaxation.
k. Runs 7->8: all 8 RFC-023 fix categories regressed simultaneously (17 PASS -> 7 PASS).

#### Code Evidence
`src/pageindex_mcp/helpers/verdict.py:125-236` (evaluate_gates): when validate_result is None and flat=True, recomputes TreeSignals from re-derived flat structure and runs only FLAT_GATE_SUBSET (3 of 10 gates). `src/pageindex_mcp/helpers/verdict.py:239-371` (apply_promotions): ~6 independent promotion branches, each an early-return PASS; only the first re-checks all_defects via `_structural_ok`. `src/pageindex_mcp/helpers/gates.py:500-504` (FLAT_GATE_SUBSET): filters to only `_FLAT_APPLICABLE_DEFECTS`. `src/pageindex_mcp/client/indexer.py:720-954` (_persist_flat_result): calls `compute_verdict(flat_structure, content_class, ..., flat=True)` WITHOUT `state.gate_result` -- the original TreeGateResult that caused flat routing is discarded. `src/pageindex_mcp/client/indexer.py:956-1106` (_persist_tree_result): calls `compute_verdict(structure, '', state.gate_result, ...)` correctly threading original TreeGateResult.

#### Key Files
- `src/pageindex_mcp/helpers/verdict.py`
- `src/pageindex_mcp/helpers/gates.py`
- `src/pageindex_mcp/client/indexer.py`

#### Simplification Proposal
**Core simplification:** Eliminate the tree/flat verdict split by making `_persist_flat_result` pass `state.gate_result` (the original TreeGateResult from `validate_tree`) through to `compute_verdict` instead of discarding it and re-deriving a 3-gate subset. This makes `evaluate_gates` always receive the full gate result regardless of routing path, so all 10 gates fire uniformly. The duplicated hysteresis block in both `_persist_tree_result` and `_persist_flat_result` (lines 851-877 and 989-1014 of `indexer.py`) gets extracted into a single function called after `compute_verdict` in both paths.

**Restructuring steps:**
- **Step A** — Thread gate_result through flat path (`indexer.py:842-848`): 1-line change adding `state.gate_result` as the third positional argument to `compute_verdict`. Delta: +1 line.
- **Step B** — Remove the flat re-derivation branch in `evaluate_gates` (`verdict.py:184-194`): delete the `if validate_result is None and flat:` block entirely once Step A guarantees `validate_result` is always passed. Delta: -11 lines.
- **Step C** — Remove `FLAT_GATE_SUBSET` and `_FLAT_APPLICABLE_DEFECTS` (`gates.py:474-504`): delete the subset, its import-time assertion, and the `flat_applicable` field if it exists solely for this purpose. Delta: -30 lines in gates.py, -1 import in verdict.py.
- **Step D** — Extract hysteresis into a shared `_apply_hysteresis()` helper, replacing the two ~15-line inline blocks in `indexer.py`. Delta: +12/-46 = net -34 lines.
- **Step E** — Remove the `flat: bool = False` kwarg from `evaluate_gates`/`compute_verdict` once Step B makes it unused. Delta: -4 lines.
- **Step F** — Update/delete tests asserting on FLAT_GATE_SUBSET behavior. Delta: -20 to +10 lines.

**Total estimated delta: -60 to -80 lines net deletion.**

**Bug classes prevented:** RFC-029 D1/D2 (3 PASS->ERROR regressions — new validate_tree reasons had no flat-path recovery routing because gate_result was discarded); Run 8->9 Haftpflicht FAIL->PASS (flat path's 3-gate subset missed low_content_density/empty_node_contamination/suspect_density); Runs 7->8 simultaneous regression of all 8 RFC-023 fix categories (tree-path-only fixes had no effect on flat-path documents); RFC-022 B1 (divergent signal derivation from empty flat blocks); RFC-025 D0 (duplicated, independently-drifting hysteresis blocks); RFC-024 D0 (threshold changes interacting differently with the two evaluation paths).

**Migration risk & sequencing:** Flat-routed documents previously escaping 7 hard-fail gates will now be subject to them — some PASS/MARGINAL docs may shift to FAIL (semantically correct, but needs corpus validation). Sequence: (1) Step D first — zero semantic change, pure refactor, deploy and verify no verdict changes; (2) Step A behind a feature flag (`UNIFIED_GATE_EVAL=false` default) — run corpus scoring in both modes, diff verdicts, validate each change is an improvement; (3) Steps B+C+E after the flag is permanently enabled — pure cleanup; (4) Step F last — align tests.

**Estimated effort:** ~4 days total (Step D: 0.5d, Step A + flag + corpus diff: 1d, Steps B+C+E: 0.5d, Step F: 1d, corpus validation between steps: 1d). Code delta is small (~80 lines deleted, ~15 added) — the risk is entirely in verdict shifts on the corpus, not code complexity.

---

### Zone 2: Garble Detection Fragmentation

**Severity:** critical | **Bug count:** 16

Garble/corruption detection is implemented as multiple independently-maintained code paths with different normalization, different Unicode range heuristics, and different call-site wiring -- creating a detection surface where each fix to one detection prong or one script family leaves blind spots in the others. The flat-path runs its own `detect_garble` (RAW_MARKDOWN normalization + module-global `_garble_config`) before the verdict's `evaluate_gates` runs a second garble evaluation (TREE_TEXT-derived TreeSignals + pipeline_config thresholds). Arabic detection depends on `expected_script` threading that is broken at multiple call sites. The bidi coherence detector (`_check_bidi_coherence` / `_reversed_morphology`) targets Unicode ranges that upstream NFKC normalization decomposes away, yielding 0% true-positive rate by construction.

#### Mechanism
Each corruption mechanism (PUA glyphs, Presentation Forms, digit-ratio noise, Latin-gibberish, reversed RTL, sparse mojibake, token-repetition) is a separate code path with its own minimum-size floor, its own Unicode range check, and its own call-site wiring. Fixing one prong (e.g., adding token-repetition to both `_tree_is_garbled` and `_flat_text_is_garbled` as RFC-010 D3/D3B did) creates a new divergence point where future fixes to one copy are not guaranteed in the other. The `expected_script` parameter -- critical for Latin-gibberish detection on Arabic documents -- is threaded through some call sites but hardcoded to None at others (`classify_verdict` hardcoded None, `_script_from_filename` returns None for German). NFKC normalization at `converters.py:2357` decomposes Presentation Forms (U+FB50-FEFF) to canonical Arabic (U+0600-06FF) before the `_reversed_morphology` detector can see them, structurally blinding the detector to its design-target population. This means every new corruption variant discovered in the corpus requires not just a new heuristic but also a wiring audit of every call site, every minimum-size floor, and every normalization ordering -- and the audit itself has repeatedly failed to catch all sites (RFC-020 F2, RFC-029 D0, RFC-033 D2).

#### History
a. RFC-010 D3/D3B: token-repetition guard added to both garble functions separately, flagged by RFC-013 D7 as fix-one-miss-the-other drift.
b. RFC-019 D2: Latin-gibberish check never activated because expected_script inferred from corrupted text itself.
c. RFC-020 F2: _script_from_filename added but returns None for German.
d. RFC-023 D0: garble-aware _text_layer_has_content caused structural reasons instead of garbling, blocking OCR escalation (D11 root cause).
e. RFC-028 D2: Arabic Presentation Forms detection too coarse, unconditionally rejected via garbling reason excluded from flat routing.
f. RFC-029 D0: _check_bidi_coherence implemented twice, never called.
g. RFC-029 D4: _repeating_token_density short-text floor made OCR retry win condition arithmetically impossible.
h. RFC-033 D2: bidi detector 0% TPR after NFKC decomposes target range.
i. RFC-034 B1-C2: confirms 0% TPR, 40% Latin mojibake undetected.
j. Run 8: expected_script parameter removed, garble detection regressed on Latin-gibberish CMap mojibake.
k. Run 9: detection partially restored but OCR escalation not wired.
l. Session memory (Jul 31): 5 distinct Arabic corruption mechanisms invisible to PUA-only heuristic.

#### Code Evidence
`src/pageindex_mcp/helpers/tree_validation.py:262-330` (validate_tree): runs every gate exhaustively via GATE_TABLE. `src/pageindex_mcp/helpers/gates.py:329-424` (GATE_TABLE): garbling is sev0 hard-fail, runs first in table order; structural checks (node_count_low, depth_low) run second/third -- so early-exit on structural reasons pre-empts garble detection on thin trees. `src/pageindex_mcp/converters/pictures.py:175-193` (zdr_egress_gate): checks pii_corpus + ZDR allowlist, but only called from 2 of ~5 egress sites. `src/pageindex_mcp/client/indexer.py:720-810` (_persist_flat_result garble section): runs detect_garble with ScriptContext + _garble_config (RAW_MARKDOWN normalization), independently from evaluate_gates' garble check.

#### Key Files
- `src/pageindex_mcp/helpers/gates.py`
- `src/pageindex_mcp/helpers/tree_validation.py`
- `src/pageindex_mcp/client/indexer.py`
- `src/pageindex_mcp/helpers/verdict.py`

#### Simplification Proposal
**Core simplification:** Collapse the two independent garble evaluation surfaces -- the flat-path `detect_garble` in `indexer.py:757-809` and the tree-path `check_garble` inside `TreeSignals.from_tree` / `evaluate_gates` -- into a single `GarbleVerdict evaluate_garble(text, script_context, config)` function that every call site invokes through one entry point. Delete `check_garble` (the backward-compat shim that rebuilds config from `os.environ` at every call) and the duplicated `ocr_noise_ratio` / `hash_pipe_ratio` / `_garble_ratio` definitions in `tree_validation.py` (identical copies of the ones in `garble.py`). Thread `ScriptContext` (with pre-NFKC `had_presentation_forms` flag) as a required parameter from the converter layer downward, eliminating the broken `expected_script=None` default.

**Restructuring steps:**
- **Step A** — Delete duplicated functions in `tree_validation.py` (lines 149-185: `ocr_noise_ratio`, `hash_pipe_ratio`, `_garble_ratio`), byte-identical to `garble.py:738-772`. Update imports. Delta: -30 lines, ~5 import lines.
- **Step B** — Delete `check_garble` shim (`garble.py:597-636`) and `_rebuild_garble_config_compat` (571-595), make `detect_garble` the sole entry point. Migrate 6 call sites in `recovery.py`/`images.py`. Delta: -45 lines, +15 lines call-site migration.
- **Step C** — Make `ScriptContext` mandatory at the indexer boundary; build it once at the top of `_process_single_file` and thread to both tree/flat paths; change `_gate_node_garbling` signature to accept `ScriptContext`. Delta: ~+10/-20 net.
- **Step D** — Merge the flat-path garble gate into `evaluate_gates` (move `indexer.py:757-809` inline check into GATE_TABLE-driven evaluation; VLM-fallback recovery stays in indexer but triggers off gate result). Delta: -40 lines indexer.py, +10 lines verdict.py.
- **Step E** — Confirm `_check_bidi_coherence` stays deleted (already removed per gates.py comment). No action needed.

**Total estimated delta: -90 to -110 net lines removed.**

**Bug classes prevented:** RFC-010 D3/D3B and RFC-013 D7 fix-one-miss-the-other drift (structurally impossible with one code path); RFC-019 D2/RFC-020 F2 (expected_script inference failures — ScriptContext built once at the boundary); RFC-023 D0 (garble-aware structural-reason aliasing eliminated by unified gate path); RFC-028 D2 (Arabic Presentation Forms coarse detection on flat path); RFC-029 D0 (bidi coherence dead code confirmed dead); RFC-033 D2/RFC-034 B1-C2 (0% TPR / 40% Latin mojibake undetected — had_presentation_forms captured pre-NFKC via ScriptContext).

**Migration risk & sequencing:** Moderate risk, heavily tested surface. Wave 1 (Steps A+E): pure refactor, no behavior change — run test suite + corpus score-diff baseline. Wave 2 (Step B): low risk, env-var-patching tests need updating to patch `_garble_config` instead. Wave 3 (Step C): medium risk — Latin-gibberish detection activates for Arabic docs for the first time; gate behind `GARBLE_MANDATORY_SCRIPT_CONTEXT` env var for one release, manually verify new detections are true positives. Wave 4 (Step D): highest risk — unifies the two evaluation surfaces; gate behind `FLAT_GARBLE_UNIFIED_GATE` env var for one release.

**Estimated effort:** Wave 1: 2-3h; Wave 2: 4-6h; Wave 3: 6-8h; Wave 4: 8-10h. Total: ~20-27 hours across 4 incremental PRs.

---

### Zone 3: Converter-Gate-Route Ordering Chain

**Severity:** critical | **Bug count:** 14

The converter selection (docling vs pymupdf4llm), OCR escalation routing, `validate_tree` gate, and `decide_route` function form a cross-subsystem chain where the ordering and string-matching gates create silent degradation paths. OCR escalation is gated on the string `'docling'` in `conv_name` -- if pymupdf4llm is primary (or docling fails and falls back), the OCR escalation gate is disarmed and a scanned/RTL PDF is converted with zero OCR. `validate_tree` then correctly detects the defect but routes to FLAT via `decide_route`, where the flat verdict's 3-gate subset can promote the thin/garbled result to PASS. Additionally, recovery methods (`_recover_rtl_repair`, `_reconvert_and_revalidate`) re-run `validate_tree` and overwrite `state.gate_result`, but `state.route` was already computed from a possibly-stale first_defect, creating a stale-routing window.

#### Mechanism
Three independently scoped gaps chain into one silent degradation path: (1) converter chain-order fragility -- OCR escalation is only reachable via the docling branch of a string-match gate, so any path through pymupdf4llm disarms the entire OCR recovery cascade; (2) the flat-gate narrowing from Zone 1 means documents that reach flat routing have their defects re-evaluated with only 3 gates; (3) recovery mixins re-run validate_tree but do not re-trigger decide_route, so a document whose defect was fixed by recovery may still follow the stale routing decision. Each fix to any one link in this chain (converter ordering, gate wiring, route decision) has historically caused regressions in the others because the chain's correctness depends on all three links being coherent, but they are owned by different modules with no shared invariant.

#### History
a. RFC-003 D3: Docling made primary, contingent on Phase 0 validation.
b. RFC-003 Amendment 3: pymupdf4llm moved to primary after Docling MPS NO-GO, fully opening AGPL gate.
c. RFC-003 Amendment 4: Docling restored as default primary via PDF_CONVERTER config.
d. RFC-005 Fix-3: OCR escalation retry on garbling reason only.
e. RFC-016 D5: D4 tree-path VLM block skipped for structural reasons (node_count<3) instead of garbling.
f. RFC-020 F2/F3: Arabic OCR lang detection broke F0 tree-path splice (zero PictureResults).
g. RFC-023 D0/D11: structural validate_tree reasons pre-empted garble check, blocking OCR escalation.
h. RFC-027 D7: chunked_docling_timeout_s never imported by worker.py (dead code).
i. RFC-028 D4: unconditional md_content overwrite on OCR retry.
j. RFC-029 D1/D2: 4 new validate_tree reasons with no recovery routing.
k. RFC-030 D1: _repeating_token_density floor made OCR retry win condition impossible.
l. RFC-032 D0-D2: inspector OCR pre-routing wired but required 16.5x timeout multiplier.

#### Code Evidence
`src/pageindex_mcp/client/indexer.py:355-718` (_convert_to_tree): contains inspector_force_ocr gate at ~368-380, converter loop dispatching on conv_name string match at ~455-517, force_full_page_ocr only threaded when conv_name=='docling'. `src/pageindex_mcp/helpers/types.py:285-320` (decide_route): performs exhaustive REASON_POLICY lookup but is called once from _convert_to_tree and its result (state.route) is never re-evaluated after recovery mixins overwrite state.gate_result. `src/pageindex_mcp/worker/errors.py:27-42` (_CHILD_ERROR_REGISTRY): maps exception class names via string matching across process boundary with no shared enum -- 'LowQualityTreeError' maps to terminal=True.

#### Key Files
- `src/pageindex_mcp/client/indexer.py`
- `src/pageindex_mcp/helpers/types.py`
- `src/pageindex_mcp/converters/pipeline.py`
- `src/pageindex_mcp/worker/errors.py`

#### Simplification Proposal
**Core simplification:** Replace the current three-phase pattern (convert -> validate+route once -> recover(mutate gate_result but maybe not route)) with a single `finalize_gate_and_route(state)` function that is the *only* writer of `state.first_defect` and `state.route`, called after every `validate_tree` invocation -- inside `_convert_to_tree`, inside `_reconvert_and_revalidate`, and inside `_recover_rtl_repair`. This eliminates the stale-routing window because route is always derived from the current gate_result, never from a cached first_defect. Separately, decouple OCR capability from converter identity by adding a `supports_ocr: bool` field to the converter chain tuples returned by `pdf_markdown_converters()`, replacing the `"docling" in conv_name` string gates.

**Restructuring steps:**
- **Step A** — Add `finalize_gate_and_route()` in `types.py` next to `decide_route` as the single writer of gate_result/ok/reason/first_defect/route. Delta: +15 lines.
- **Step B** — Wire it into all validate_tree call sites: `_convert_to_tree` (-6 lines), `_reconvert_and_revalidate` (-2 lines), `recovery.py:_recover_rtl_repair` (-2 lines).
- **Step C** — Remove the ad-hoc re-derivation in the recovery loop (`indexer.py:1210-1218`, the `_pre_route` conditional). Delta: -8 lines.
- **Step D** — Remove the workaround match arms for stale routes (`indexer.py:1247-1260`), which become dead code once ok=True always implies route=TREE. Delta: -12 lines.
- **Step E** — Converter chain OCR capability flag: change `pdf_markdown_converters()` to return `(name, callable, supports_ocr)` tuples; replace the three `"docling" in conv_name` string-match branches with a `supports_ocr` check. Delta: -7 lines net in indexer.py, +5 in pipeline.py.
- **Step F** — Move `_defect_from_reason_str` into `types.py` next to `finalize_gate_and_route`. Net 0.

**Total estimated net delta: -40 to -50 lines across 4 files.**

**Bug classes prevented:** RFC-029 D1/D2 (new TreeDefects auto-routed via finalize_gate_and_route; missing REASON_POLICY entry now fails fast instead of silently stale-routing); RFC-028 D4 (stale route corrected before persist, preventing wrong-route overwrite); RFC-023 D0/D11 (initial route computation uses the same finalize path as recovery, no separate stale code path); the (True, Route.FLAT)/(True, Route.REJECT) workaround arms (never fire once routing is always current); pymupdf4llm-as-primary OCR disarming (supports_ocr flag prevents silent fallthrough).

**Migration risk & sequencing:** Moderate risk — touches the hottest ingestion code path. Sequence: (1) Steps A+F first (pure addition, no behavior change) — ship, run corpus ingest, confirm identical verdicts; (2) Step B one file at a time, starting with `_reconvert_and_revalidate` (highest impact), corpus diff after each; (3) Step C only after B is stable; (4) Step D only after C confirms no stale routes reach the match block (add a temporary assertion for one corpus run first); (5) Step E independent, can run in parallel, test with pymupdf4llm-as-primary configuration.

**Estimated effort:** Steps A-D: 1-2 days implementation + 1 day corpus validation; Step E: 0.5 day implementation + 0.5 day validation; Step F: 0.5 day. Total: 3-4 engineering days.

---

### Zone 4: Registry Dual-Write Consistency

**Severity:** high | **Bug count:** 8

Document metadata is written to two independent stores (MinIO sidecar .meta.json + Postgres registry) via separate code paths with no transactional guarantee. The dual-write topology is further complicated by a mode flag (`registry_verdict_authority`) that simultaneously changes both the write order and the write-barrier behavior: when set to 'postgres', it skips the MinIO read-after-write barrier (`_confirm_write_visible`) while switching to a 3-step CAS-guarded Postgres-first path where verdict fields are written twice per call (upsert_verdict then folded into upsert_doc). Failures are swallowed (best-effort), and correctness is backstopped only by a ~1200s reconcile cron that independently re-diffs MinIO against Postgres. The erasure cascade (delete_doc) is a 7-step non-transactional sequence where individual store failures are collected in an errors[] list but never raised -- returning HTTP 200 regardless.

#### Mechanism
Every write to document metadata must succeed in both MinIO and Postgres to be fully consistent, but the two writes are independent, non-transactional, and can fail independently. A Postgres write failure is swallowed and queued for retry in the verdict-retry backlog, drained only on the ~1200s reconcile cron tick. A MinIO write failure can leave the sidecar stale while the Postgres row is current, or vice versa. The registry_verdict_authority flag controls both the write-barrier (latency vs safety tradeoff) and the write topology (which store is written first) with a single toggle, making it impossible to tune one without affecting the other. The reconcile cron's stale-row deletion uses a 10-minute grace window and a 50% safety cap, but its _delete_stale_rows calls the FULL HR2 erasure cascade (not just a Postgres row delete) -- so a slow ingest taking >10min could trigger full document deletion on a legitimately in-flight document. The backfill's registry:complete flag, once set, makes run_auto_backfill a permanent no-op, but the flag can be set on zero results (RFC-007 ISS-03: entire corpus becomes invisible to query tools).

#### History
a. RFC-002 Amendment 2: storage.py delete_doc reversed HR2 cascade order, never cleared hash-cache.
b. RFC-006 D3: Postgres registry delete was fire-and-forget, logging success regardless of outcome (RFC-007 ISS-02).
c. RFC-006/RFC-007 ISS-03: backfill set registry:complete on zero meta_keys, hiding entire corpus.
d. RFC-025 D0: hysteresis structurally dead on reingestion because corpus wipe deletes prior meta.json sidecars (RFC-026 D3).
e. RFC-034 D18: write-visibility barrier over-provisioned at 4.4s (8.8s worst-case), causing Arabic SLA doc 3-5 minute delay.
f. RFC-036 D1: reduced to 0.45s and wrapped PersistenceNotVisibleError.
g. RFC-034 D19: enrichment fix staged in git but never committed, inactive during Run 19.

#### Code Evidence
`src/pageindex_mcp/worker/registry_mirror.py:55-158` (_upsert_registry_row): 3-step CAS-guarded Postgres-first path when registry_verdict_authority=='postgres', with verdict written via upsert_verdict then folded again into upsert_doc -- verdict fields written twice per call. Failures swallowed in outer except. `src/pageindex_mcp/storage/documents.py:141-305` (delete_doc): 7-step cascade, noqa: C901/PLR0915 grandfathered, 165 lines, cx41. Never raises; returns {errors: [...]}. Step 6 skipped silently when registry_enabled/postgres_dsn missing with no errors[] entry. `src/pageindex_mcp/worker/job.py:107-396` (process_document_job): calls _upsert_registry_row after job already marked DONE and UPLOADS success already incremented; registry write failure never touches job_status.

#### Key Files
- `src/pageindex_mcp/worker/registry_mirror.py`
- `src/pageindex_mcp/storage/documents.py`
- `src/pageindex_mcp/storage/verdict.py`
- `src/pageindex_mcp/registry_backfill/reconcile.py`

#### Simplification Proposal
**Core simplification:** Eliminate the `registry_verdict_authority` mode flag entirely by making Postgres the sole verdict-authority path and making MinIO the append-only archival sidecar that is always written second, best-effort. The dual-write in `_upsert_registry_row` collapses from two branching code paths (91-143) into a single linear sequence: (a) upsert_verdict to Postgres, (b) upsert_doc to Postgres with the winning fields, (c) best-effort sidecar backfill to MinIO -- no conditional write-barrier, no mode-dependent topology.

**Restructuring steps:**
- **Step 1** — Collapse `_upsert_registry_row` into a single linear path (`registry_mirror.py`), removing the mode branch. Delta: ~-25 lines.
- **Step 2** — Remove `registry_verdict_authority` config and validation (`config.py`), and its 3 call sites. Delta: ~-20 lines across config.py, verdict.py, reconcile.py.
- **Step 3** — Remove the write-visibility barrier from sidecar writes in `save_doc_meta` (`verdict.py:232-233`) -- sidecar is now archival, reads go through Postgres. Delta: ~-4 lines.
- **Step 4** — Make `read_registry_fields` source verdict from Postgres, not the MinIO sidecar fallback, with graceful degradation when Postgres is unavailable. Delta: ~-10/+5 lines.
- **Step 5** — Unconditionally drain the verdict-retry queue in `reconcile.py` (remove the mode guard). Delta: ~-2 lines.
- **Step 6** — Add an errors[] entry for the silent-skip case in `delete_doc` when registry_enabled/postgres_dsn is missing. Delta: ~+2 lines.
- **Step 7** — Fold `upsert_verdict` into `upsert_doc` as a single CAS-guarded SQL statement, eliminating the double verdict write. Delta: ~+10/-20 lines in `registry/queries.py`.

**Total rough delta: -50 to -60 lines net.**

**Bug classes prevented:** RFC-034 D18/RFC-036 D1 (write-barrier latency tuning axis eliminated entirely); RFC-006 D3 (fire-and-forget Postgres delete now observable via errors[]); RFC-007 ISS-03 (Postgres as sole authority removes MinIO-listing gate on visibility, though the backfill flag itself may need a separate fix); the "verdict written twice" race (eliminated by combined CAS-guarded SQL); the mode-flag coupling of latency vs safety (eliminated — no mode flag).

**Migration risk & sequencing:** Main risk is deployments running with `registry_verdict_authority=minio` (default) and no reachable Postgres pool — removing the flag forces Postgres as authority. Sequence over 4 weeks: Week 1 — Steps 5-6 (safe, no behavior change); Week 2 — Step 3 (safe, reconcile cron already heals sidecar drift); Week 3 — Steps 1-2 (collapse dual path, remove config flag, with a deprecation warning for one release before hard removal); Week 4 — Steps 4 and 7 (Postgres-sourced reads, combined SQL, after Steps 1-2 are stable).

**Estimated effort:** 3-4 developer-days across the 4-week rollout. Step 7's SQL refactor is highest-risk (~1 day including CAS integration tests). Steps 1-2 ~1 day. Steps 3-6 ~0.5 day each.

---

### Zone 5: Worker-Child Process Boundary

**Severity:** high | **Bug count:** 5

The worker parent and converter child communicate through a process boundary using string-matched exception class names (_CHILD_ERROR_REGISTRY), stdout JSON for results, and two parallel notions of job outcome (arq return/exception vs Redis job-status hash). The LOW_QUALITY_TREES counter incremented in the child subprocess never reaches /metrics because metrics/sync.py's _BRIDGED_METRICS omits it. Terminal child errors are deliberately swallowed (return '') so arq records success while the Redis hash says error/low_quality_tree -- a consumer trusting arq alone misreads a rejection as success.

#### Mechanism
The process boundary serializes exceptions as class-name strings with no shared enum or type definition. A renamed exception class falls through to _DEFAULT_CHILD_CLASSIFICATION (terminal=False) and is retried MAX_TRIES times then DLQ-pushed, instead of failing fast as intended. The two parallel outcome channels (arq job result vs Redis status hash) are never reconciled: the terminal-child-error branch sets Redis status=error/reason but returns '' (not raises), so arq records normal completion. The effective_timeout_at field is written directly via redis.hset, bypassing _set_job_status's transition validator entirely -- two mutation disciplines on one hash depending on which field is being written. The reap_stale_jobs cron can race with a completing child, flipping an in-flight job to ERROR at the same moment its child finishes successfully -- resolved by making ERROR->DONE a legal transition, but the two async writers are otherwise uncoordinated (no lock). Metrics for the most important quality signal (LOW_QUALITY_TREES) are orphaned in the child subprocess and never bridged to the parent's /metrics endpoint.

#### History
a. RFC-027 D7: chunked_docling_timeout_s function existed in converters.py but was never imported by worker.py (dead code across process boundary), causing world-stats-pocketbook ERROR for 3 consecutive runs.
b. RFC-028 D0: wiring fix confirmed the function was present but unreachable.
c. RFC-034 D18: write-visibility barrier raised PersistenceNotVisibleError as RuntimeError propagating to child process as non-terminal (arq retried).
d. RFC-036 D1: wrapped in try/except.
e. The _CHILD_ERROR_REGISTRY string-match approach is documented in the code map as ordering-dependent with no shared enum.

#### Code Evidence
`src/pageindex_mcp/worker/errors.py:27-42` (_CHILD_ERROR_REGISTRY): dict mapping exception class name strings to ChildErrorClassification(reason, terminal); 'LowQualityTreeError' -> terminal=True, 'RuntimeError' -> terminal=False. `src/pageindex_mcp/worker/job.py:107-396` (process_document_job): on terminal reason, sets Redis status=error but returns '' (line ~244-252), so arq records success. Zone 6 Part B: child_effective_timeout written via raw redis.hset bypassing _set_job_status transition validator. Late-success path: ERROR->DONE made legal with late_success/reaped_recovery flags.

#### Key Files
- `src/pageindex_mcp/worker/job.py`
- `src/pageindex_mcp/worker/errors.py`
- `src/pageindex_mcp/worker/subprocess_mgr.py`

#### Simplification Proposal
**Core simplification:** Eliminate the two-channel outcome split by making `process_document_job` always raise on failure (never `return ""` for terminal errors), so arq's job result and the Redis status hash agree on whether a job succeeded or failed. Replace the string-keyed `_CHILD_ERROR_REGISTRY` dict with a shared `ChildReason` StrEnum used by both the child's JSON emission and the parent's classification, so a renamed or new exception class is a compile-time error rather than a silent fall-through. Add `low_quality_trees_total` (with its `reason` label) to `_BRIDGED_METRICS` so the child's quality-gate counter reaches `/metrics`.

**Restructuring steps:**
- **Step A** — New `src/pageindex_mcp/worker/reasons.py` with a `ChildReason(StrEnum)` (LOW_QUALITY_TREE, CONVERTER_TIMEOUT, CONVERTER_OOM, LLM_FAILURE_TERMINAL, LLM_FAILURE_TRANSIENT, CONVERTER_CHILD_FAILED, INPUT_MISSING, CONVERTER_ENV_MISSING) plus a `_TERMINAL_REASONS` set. Child emits `ChildReason` values directly instead of `type(exc).__name__`; `_CHILD_ERROR_REGISTRY` is replaced or deleted. Delta: +40 (reasons.py), -20 (errors.py), ~10 changed in converters_cli.py. Net ~+30.
- **Step B** — Eliminate return-empty-string-on-terminal-error (`job.py:234-244`); raise `TerminalChildError` instead, caught in the outer except and routed to DLQ-final-attempt logic. Delta: ~-10 lines.
- **Step C** — Route `effective_timeout_at` through `_set_job_status` (add a PROCESSING->PROCESSING self-transition, or an `update_fields` helper) instead of raw `redis.hset`. Delta: ~+5/-2 lines.
- **Step D** — Bridge LOW_QUALITY_TREES to `/metrics`: add entries to `_BRIDGED_METRICS` in `sync.py`, call `_mirror_bridged_incr` alongside `LOW_QUALITY_TREES.labels(...).inc()` in indexer.py/images.py. Delta: ~+9 lines.
- **Step E** — Remove the late-success safety net (ERROR->DONE transition, late_success/reaped_recovery flags) once Step B narrows the reaper race. Delta: ~-20 lines job.py, -2 job_status.py.

**Total estimated delta: ~+5 to -10 lines net.**

**Bug classes prevented:** RFC-027 D7/RFC-028 D0 (shared enum surfaces unreachable functions at import time, not after 3 ERROR runs); RFC-034 D18 (child cannot silently misclassify a new exception as RuntimeError); RFC-036 D1 (arq always records failures as failures — no misread as success); LOW_QUALITY_TREES metric orphaning (closed directly); reaper race / ERROR->DONE complexity (narrowed once terminal errors always raise).

**Migration risk & sequencing:** Moderate — arq outcome semantics change (terminal errors now raise instead of returning ""), affecting monitoring keyed on arq success/failure counts. Sequence: (1) Step D first — zero behavioral change, deployable immediately; (2) Step A — child and parent deployed together (same container image); old string-match registry kept as fallback for one release cycle then deleted; (3) Step C — purely internal; (4) Step B — behind `TERMINAL_ERRORS_RAISE=true` feature flag for one cycle, with alerting updated simultaneously; (5) Step E last — only after Step B has soaked in production long enough to confirm the reaper race no longer produces ERROR->DONE transitions.

**Estimated effort:** Step D: 1h; Step A: 4-6h; Step C: 2h; Step B: 3-4h; Step E: 2h. Total: ~12-15 hours across 3-4 PRs over 1-2 sprints (to allow production soak between Steps B and E).

---

### Zone 6: ZDR/PII Egress Gap

**Severity:** high | **Bug count:** 3

Hard Rule 3 (ZDR routing for PII corpora) is enforced as a per-call-site opt-in pattern (zdr_egress_gate) rather than a single choke point. The two highest-volume LLM egress call sites -- core tree-generation (_run_md_to_tree, _run_page_index_retrying) which send FULL document text on every ingestion, and the VLM garble-fallback rasterization path -- do not call zdr_egress_gate at all. LLM_FALLBACK_BASE_URL is never validated against the ZDR allowlist anywhere in the codebase, meaning the retry/fallback mechanism can silently violate HR3 for full document text under exactly the failure conditions (primary endpoint down/throttled) an operator would configure a fallback for.

#### Mechanism
The ZDR check (_is_zdr_allowlisted) runs once at server startup time against OPENAI_BASE_URL when pii_corpus=true. At runtime, zdr_egress_gate re-checks the same settings but is only called from 2 of ~5 LLM egress sites (picture descriptions and flat-doc descriptions). The core tree-generation calls go through _llm_with_retry, which on retry exhaustion silently repoints api_base to the ungated LLM_FALLBACK_BASE_URL for the same full-document call. Any 429/5xx/timeout from the primary ZDR endpoint triggers this fallback path -- precisely the failure mode HR3 is meant to prevent. The VLM fallback path (vlm_extract_markdown) rasterizes full PDF pages and sends via get_openai_client() with no per-call zdr_egress_gate check. This is an architectural gap (no single enforcement choke point), not an implementation bug in any one call site.

#### History
a. RFC-016: enabled VLM_FALLBACK=true in .env without a fresh RFC, despite RFC-004 Amendment 5 locking VLM_MODE=disabled and requiring GPU + ZDR/EU endpoint.
b. Session memory (Jul 17): CONFLICT between RFC-004 lock and RFC-016 enablement.
c. The code map's cross-subsystem interaction analysis independently identified that _run_md_to_tree and _run_page_index_retrying send FULL document text and do not call zdr_egress_gate, that LLM_FALLBACK_BASE_URL is never checked against ZDR allowlist, and that the startup-only check means runtime process-lifetime drift is undetected.

#### Code Evidence
`src/pageindex_mcp/client/llm.py:49-127` (_llm_with_retry): after max_retries exhausted, calls call_fn(base_url=fallback_base_url) where fallback_base_url defaults to _LLM_FALLBACK_BASE_URL (line 53) which is os.getenv('LLM_FALLBACK_BASE_URL', '') -- never checked against ZDR. `src/pageindex_mcp/converters/pictures.py:175-193` (zdr_egress_gate): checks pii_corpus + _is_zdr_allowlisted(api_base), but trace_path confirms only 2 callers (_add_vlm_descriptions and _generate_flat_doc_description). `src/pageindex_mcp/client/llm.py` callers: _run_md_to_tree and _run_page_index_retrying (confirmed via trace_path inbound) -- both send full document text, neither calls zdr_egress_gate.

#### Key Files
- `src/pageindex_mcp/client/llm.py`
- `src/pageindex_mcp/converters/pictures.py`
- `src/pageindex_mcp/config.py`
- `src/pageindex_mcp/server.py`

#### Simplification Proposal
No simplification proposal was returned for this zone in the source data.

---

### Zone 7: Arabic/RTL Pipeline Blindness

**Severity:** high | **Bug count:** 14

Arabic and RTL text processing is the least-tested, highest-risk surface in the codebase, with multiple independently-broken detection, extraction, and heading-injection subsystems. Arabic heading injection (_inject_arabic_structural_headings) over-qualifies for validate_tree thresholds, blocking richer flat fallback. OCR language detection reads the document FILENAME (not content), so Arabic scans with English filenames never get 'ara' added to Tesseract's language list on the escalation path designed to catch them. Table RTL detection (table_is_rtl) is re-evaluated per continuation merge and can flip branches mid-document for borderline Arabic-character ratios. The fence-parity toggle in flat extraction (RFC-029 D3) destroyed content for Arabic documents because Docling's layout misclassification of stamps/signatures produced stray fence lines.

#### Mechanism
Each Arabic-specific fix creates new interactions with Latin-centric assumptions elsewhere in the pipeline. Heading injection (RFC-028 D1) was calibrated to create hierarchy for Arabic docs, but injecting just enough headings to clear validate_tree's depth>=2 threshold also blocked the flat fallback path that would have preserved 3-5x more content (marsoom 13: 1225 chars tree vs 5972 chars flat). OCR language override (_script_from_filename) correctly detected Arabic but forced OCR, causing Docling to reclassify PictureItems as TextItems, zeroing PictureResults and collapsing tree-routed docs to flat routing. The ensure_tessdata fallback silently substitutes deu+eng when Arabic tessdata is unavailable, producing Latin-mode OCR on Arabic pages whose output passes the garble gate because it no longer looks Arabic. The bidi coherence detector's 0% TPR (Zone 2) means reversed-RTL content is invisible to all automated quality checks. These are not independent bugs but a single architectural problem: the pipeline was designed for Latin-script German T&C documents and every Arabic-specific addition interacts badly with Latin-centric assumptions it cannot see.

#### History
a. RFC-005 Fix-1: Arabic legal headings rejected by _segment_label's Latin-only gates.
b. RFC-013 D6: missing non-Latin tessdata.
c. RFC-020 F2/F3: Arabic OCR lang detection broke F0 tree-path splice.
d. RFC-028 D1: Arabic heading injection blocked richer flat extraction.
e. RFC-028 D4: OCR retry unconditionally overwrites content, al-qarar regressed 230->123 chars.
f. RFC-028 D5: vastly more OCR diluted garble signals (warid-597: 1.8k->54k chars, digit-ratio ~100% to <1%, MARGINAL->PASS).
g. RFC-029 D3: fence-parity toggle destroyed SLA (264->0 blocks), MOU (89% loss), qerar-106, marsoom-13.
h. RFC-029 D1: content-density gate rejected Penal Code (408 chars/node), federal_decree_law (54 chars/node).
i. Run 13: MOU collapsed 166->20 nodes, SLA/marsoom-13 went to 0 chars, warid-597 timeout/hang.

#### Code Evidence
`src/pageindex_mcp/helpers/types.py:285-320` (decide_route): NODE_COUNT_LOW and DEPTH_LOW route to FLAT when flat_routing_enabled, but heading injection can push Arabic docs just above the threshold into TREE routing with less content. `src/pageindex_mcp/worker/errors.py:27-42` (_CHILD_ERROR_REGISTRY): 'TessdataUnavailableError' maps to terminal=True, but ensure_tessdata silently substitutes deu+eng rather than raising this error for Arabic. `src/pageindex_mcp/helpers/flat.py:26-172` (route_and_extract_flat): single-pass line scan for content_class classification; fence-toggle vulnerability existed here per RFC-029 D3 / RFC-030 D0.

#### Key Files
- `src/pageindex_mcp/client/indexer.py`
- `src/pageindex_mcp/converters/pipeline.py`
- `src/pageindex_mcp/helpers/flat.py`
- `src/pageindex_mcp/helpers/tree_validation.py`

#### Simplification Proposal
No simplification proposal was returned for this zone in the source data.

---

### Zone 8: Duplicated Convergent Logic

**Severity:** medium | **Bug count:** 6

Multiple independent code paths compute the same derived value (flat-block text, garble detection, verdict hysteresis, route_and_extract_flat, table-text extraction) with subtly different implementations that converge on the same state. Three near-identical functions (_flat_block_primary_text, _flat_block_text, _flat_search_text) each reimplement the table->join(row_records) branch. The verdict-ledger hysteresis block is copy-pasted in both _persist_tree_result and _persist_flat_result. route_and_extract_flat is re-invoked 2-3 times per ingestion across recovery mixins and enrichment, with the first runs thrown away after comparison. flat_doc_view re-derives row_records at every read, duplicating what route_and_extract_flat + stitch computed at ingestion.

#### Mechanism
When the same logic exists in N places, a fix to one copy is not guaranteed in the others. This is not just code duplication -- it is convergent duplication where the copies produce values that flow into the same downstream consumer (e.g., the verdict sidecar), creating silent disagreements when one copy is updated and the others are not. The token-repetition guard (RFC-010 D3/D3B) was explicitly flagged as this pattern by RFC-013 D7. The route_and_extract_flat triple-invocation (recovery comparison + enrichment + real persistence) means the full parse+stitch pipeline runs 3 times from scratch for one ingestion, with each invocation potentially producing different results if the underlying markdown was modified between calls (e.g., by splice_figure_markers). Continuation-table stitching is only invoked from the flat path, so the same paginated table inside a tree document's node text is never re-joined.

#### History
a. RFC-010 D3/D3B: token-repetition guard added separately to _tree_is_garbled and _flat_text_is_garbled, flagged by RFC-013 D7 as fix-one-miss-the-other drift.
b. RFC-015 D3: _tree_max_leaf_ratio counting non-leaf wrappers in denominator.
c. Session memory ISS-36: digit-ratio check duplicated at helpers.py lines 534-538 and 1072-1075.
d. The code map identifies _flat_block_primary_text, _flat_block_text, _flat_search_text as three near-identical reimplementations, verdict-ledger hysteresis as copy-pasted in both persistence methods, and route_and_extract_flat as re-invoked across recovery mixins.

#### Code Evidence
`src/pageindex_mcp/client/indexer.py:720-954` (_persist_flat_result) and `:956-1106` (_persist_tree_result): both contain ~15-line _LEDGER_PRIORITY/read_verdict_ledger hysteresis blocks. `src/pageindex_mcp/helpers/flat.py:26-172` (route_and_extract_flat): invoked from _recover_flat_prefer (recovery.py:568), _recover_rtl_flat_compare (recovery.py:468), _apply_picture_enrichment (images.py:159,185), each independently re-running the full parse+stitch pipeline.

#### Key Files
- `src/pageindex_mcp/helpers/flat.py`
- `src/pageindex_mcp/client/indexer.py`
- `src/pageindex_mcp/client/recovery.py`
- `src/pageindex_mcp/client/images.py`

#### Simplification Proposal
No simplification proposal was returned for this zone in the source data.

## Cross-Cutting Themes

- Spec-vs-code divergence: governance/RFC prose repeatedly diverges from actual implementation (module DAG edges, cache/storage read-through direction, transport bypassing service layer, en-dash normalization, validate_tree existence) — later RFCs discover the spec was verified against prose, not source.
- Designed primary mechanisms are frequently never implemented, leaving the documented fallback as the de facto primary path (PyPDF2 fallback-as-primary; PDF-inspector classification computed but dead-ended before wiring to routing).
- Quality gates (validate_tree, garble detection, HR5) are repeatedly hardened, then repeatedly bypassed by "promotion" paths (image_enrichment_promoted, cat_b_promoted) that let near-zero-content or garbled documents PASS, requiring successive content-quality guards to close each bypass.
- Garble/script detection has chronic blind spots specific to Arabic and mixed-script text: PUA-only heuristics miss reversed RTL titles, Presentation-Forms encoding, and valid-Unicode word-splitting; bidi coherence detectors target Unicode ranges that upstream NFKC normalization or python-bidi's own output never populate, yielding 0% true-positive rate; expected_script is inconsistently threaded through call sites so checks silently no-op.
- Ordering/early-exit bugs repeatedly misroute failures: validate_tree's structural checks (node_count<3/depth<2) run before or instead of the garble check, so genuinely garbled documents get a "structural" reason code that OCR-escalation logic (gated on reason=='garbling') never sees, blocking recovery until each instance is separately re-wired (RFC-023 D0/D11, RFC-030 D2, and independently in session-memory observations on the same bug).
- Interim/narrow fixes (regex requiring non-whitespace boundaries, size-gate coupling, single-check duplication across two garble functions, threshold widening as a jitter stopgap) require follow-on RFCs to generalize or replace once corpus testing exposes their edge cases; threshold-widening in particular was explicitly flagged as insufficient by its own authors and required hysteresis/anchoring instead.
- Multi-pass / multi-pipeline interactions collide: page-level OCR escalation and per-picture OCR compete and conflate (text reclassified from prose to image blocks); tree-path and flat-path picture enrichment diverge because a fix applied to one path (flat) is never extended to the other (tree) until a picture-heavy fixture surfaces the gap; a single OCR_ESCALATION toggle controls two unrelated mechanisms.
- Feature flags and infrastructure are frequently landed but never wired to their consumers (chunked-Docling timeout function never called by worker.py; PDF_INSPECTOR_PRECLASSIFY flag unconsumed; _check_bidi_coherence defined twice but never called; RFC-034 D19 fix staged in git but never committed) — "dead code" bugs that persist across multiple audit runs until specifically diagnosed as wiring omissions.
- Non-transactional multi-step writes and async fire-and-forget operations create silent consistency violations (erasure cascade ordering reversed and hash-cache never cleared; registry delete not awaited so cascade logs success on failure; backfill sets a completion flag even on zero keys, hiding the entire corpus from queries) — all violating Hard Rule 2 or Hard Rule 5 in spirit even though the code "succeeds".
- Fixes narrowly scoped to one corpus symptom cause regressions elsewhere in the same or a related document class: heading-injection tuned to create hierarchy also over-qualifies for validate_tree, blocking a richer flat fallback; a splitter/table-repair change shared between orientations or between OCR-lang-detection improves one metric (language coverage) while diluting another (garble-ratio signal) enough to mask remaining junk content.
- Session-memory (real-time observations) and RFC documents frequently converge on the exact same root cause and even the same specific documents/char-counts from independent evidence trails, confirming these are not independent bugs but the same regression observed by different audit mechanisms.
- Governance/compliance locks are occasionally overridden by later feature work without reconciliation (RFC-004's VLM_MODE=disabled lock bypassed by RFC-016 enabling VLM_FALLBACK in production without a fresh RFC, GPU requirement, or ZDR/EU endpoint verification), and documentation (README architecture diagrams) goes stale relative to actual pipeline behavior after such changes.
- Coordinated/simultaneous regressions across many unrelated symptom clusters in a single run point to lost or reverted commits (uncommitted RFC-023 D0-D11 batch apparently discarded in a rebase/reset) rather than independent bugs, a pattern distinguishable from genuine content-loss regressions by the synchronized timing of the symptom onset.
