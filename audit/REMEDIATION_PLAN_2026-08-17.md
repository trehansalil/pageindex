# Remediation Plan — 2026-08-17

**Audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-17_POST-FIX-4.md
**Zones:** 1 of 8 (top by priority)
**Waves:** 1

## Priority Scores

| Rank | Zone | Score | Severity | Bug Count | Proposal Status | Note |
|---|---|---|---|---|---|---|
| 1 | Zone 1: Garble Detection Hydra | 57.6 | critical | 12 | no_proposal (delta undefined) | Selected for this plan. Recent commit `7b345c4` ("consolidate garble detection") may have partly addressed this — re-run with explicit delta to confirm not stale. |
| 2 | Zone 2: God Function Routing Cascade (client.py index()) | 52.8 | critical | 11 | no_proposal (delta undefined) | Commit `646cdc0` ("decompose 1365-line index() into recovery pipeline + orchestrator") suggests likely CLOSED or substantially mitigated — verify before prioritizing. |
| 3 | Zone 5: OCR/Enrichment Signal Conflation | 32.4 | high | 9 | no_proposal (delta undefined) | Commit `f37584e` ("split OCR_ESCALATION into garble/per-picture, add primary_text, unify enrichment path") suggests likely landed/closed — verify. |
| 4 | Zone 4: Threshold Calibration Feedback Loops | 28.8 | high | 8 | no_proposal (delta undefined) | No matching landed-fix commit found — likely still open. |
| 5 | Zone 3: Verdict Persistence Split-Brain | 25.2 | high | 7 | no_proposal (delta undefined) | No matching landed-fix commit found — likely still open. |
| 5 | Zone 6: Conversion Pipeline Stage Coupling (pdf_to_markdown_docling) | 25.2 | high | 7 | no_proposal (delta undefined) | Tied with Zone 3. No matching landed-fix commit found — treat as open. |
| 7 | Zone 7: Registry/Persistence Consistency Gaps | 14.4 | medium | 6 | no_proposal (delta undefined) | Real data-loss risk (stale-row-deleted-after-fresh-write) but lower priority by formula. |
| 7 | Zone 8: Dead/Uncommitted/Stale Code Divergence | 14.4 | medium | 6 | no_proposal (delta undefined) | Cleanup/hygiene risk; stale-deployed-image bidi issue deserves a quick deploy-verification check independent of this score. |

All zones scored at the conservative 1.2x no-delta multiplier — no `zone-delta-analysis` was supplied for this run. Re-running with explicit deltas would sharpen these scores and could move several zones (2, 5, and possibly others) out of the active queue entirely given the git-log evidence of landed fixes.

Only **Zone 1** carried a validated fix spec in this run's input; the remaining 7 zones are listed for backlog visibility but have no fix spec below.

## Wave Sequence

### Wave 1
- **Zones:** Zone 1: Garble Detection Hydra
- **Rationale:** Single zone to fix. Zone 1 touches `helpers.py` (primary), `converters.py`, and `client.py`. No other zones in this batch compete for the same files, so it runs alone in wave 1 with no conflict or dependency concerns.
- **Shared file conflicts:** none

## Fix Specs

### Zone: Zone 1: Garble Detection Hydra (wave 1, priority 1)

**Severity:** critical · **Estimated complexity:** medium · **Depends on:** none

#### Mechanism to eliminate

Five garble evaluation paths use inconsistent `expected_script` derivation: `TreeSignals.from_tree` and `_gate_node_garbling` use the filename-derived `expected_script` from `client.py`, but the three `converters.py` callsites (`_text_layer_has_content` at line 1652, `_document_level_text_fallback` at line 1751, region garble check at line 2153) derive `expected_script` locally via `infer_script(text)` from the TEXT being checked. A garbled Arabic PDF whose text layer contains Latin garbage infers `expected_script="Latn"`, bypassing the `latin_gibberish` prong entirely.

Additionally, `_garble_ratio` (line 1958) performs windowed 2000-char chunking that can diverge from the single-shot `check_garble` in `TreeSignals.from_tree` (the RFC-033 D1 tautology class), and `_gate_node_garbling` (line 1626) calls `_garble_check_nodes` with per-node `_infer_script` override logic (line 1537-1555, QF3) that can produce inconsistent garble signals within a single tree evaluation.

#### Strategy

Thread filename-derived `expected_script` through `converters.py` garble-checking functions as an explicit parameter (currently absent), so all 7+ garble evaluation sites use the same script context. Simplify `_garble_ratio` to eliminate windowed divergence from the bulk check. Remove the `_infer_script` override in `_garble_check_nodes` for nodes shorter than 50 chars (where it falls back to `page_script` anyway) to reduce inconsistency surface area. All changes preserve the `check_garble` single entry point already landed.

> **Validation flagged this strategy as `needs_work` (not approved) — see Validation Results below before implementing.** In particular, the primary ingestion route (`pdf_markdown_converters()` chain) and two more callsites (subprocess worker, chunked large-PDF route) are missing from the code targets, the `_garble_ratio` target is internally contradictory, and the remote-Docling default profile bypasses this fix entirely.

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/converters.py` | 1636-1654 | Add `expected_script` parameter to `_text_layer_has_content` | Add optional `expected_script: str \| None = None` to the signature (line 1636). When provided, use it instead of `infer_script(text)` at line 1652. When `None`, fall back to current `infer_script(text)` behavior. | Called from `_recover_picture_text` at line 2158 (must update that callsite) and possibly elsewhere. Default `None` preserves backward compatibility. |
| `src/pageindex_mcp/converters.py` | 1699-1751, 3432 | Add `expected_script` parameter to `_document_level_text_fallback` | Add optional `expected_script: str \| None = None` to the signature (line 1699). At line 1751, use `expected_script` if provided, else fall back to `infer_script(full_text)`. Update the `functools.partial` callsite at line 3432 to thread `expected_script` from `pdf_to_markdown_docling`. | Called via `functools.partial` in `post_fallback_stages` (line 3432) — the partial must include `expected_script`. Update docstrings. |
| `src/pageindex_mcp/converters.py` | 2062-2158, 2539 | Thread `expected_script` to region garble check in `_recover_picture_text` | Add optional `expected_script: str \| None = None` to `_recover_picture_text` (line 2062). At line 2153, use `expected_script` if provided instead of `infer_script(region_text)`. At line 2158, pass `expected_script` to `_text_layer_has_content`. Update caller at line 2539 (`_recover_picture_results` calls `_recover_picture_text`) to forward `expected_script`. | Region check at 2143-2156 must still work when `expected_script` is `None` (backward compat for non-Arabic docs). |
| `src/pageindex_mcp/converters.py` | 3141-3146, 3432, 3447-3449 | Add `expected_script` parameter to `pdf_to_markdown_docling` and thread it through | Add optional `expected_script: str \| None = None` to `pdf_to_markdown_docling` (line 3141). Thread through to `_document_level_text_fallback` (partial at 3432), `_recover_picture_results` (line 3447), and downstream garble-checking functions — this is the top-level entry point where filename-derived script context enters the converter pipeline. | Called from `client.py` (lines 1316, 1621), which already has `expected_script` in scope. Client.py callsites must be updated. Return type must not change. **See Validation blocker: these two callsites are the force-OCR escalation paths, not the primary route (see below).** |
| `src/pageindex_mcp/client.py` | 1316, 1621 | Pass `expected_script` to `pdf_to_markdown_docling` calls | At lines 1316 and 1621, add `expected_script=expected_script` to the call. `expected_script` is already in scope (derived from `_script_from_filename` at line 2053). | `expected_script` is computed once at line 2053 and threaded through all `index()` submethods; both callsites must use the same variable. |
| `src/pageindex_mcp/helpers.py` | 1958-1976 | Simplify `_garble_ratio` to eliminate windowed divergence | Refactor `_garble_ratio` to use consistent `check_garble` evaluation instead of independently re-chunking flat text into 2000-char windows, which can diverge from the single-shot `check_garble` in `TreeSignals.from_tree` (RFC-033 D1 tautology class). | Feeds `sig.garble_ratio` / `sig.effectively_garbled`, used by `classify_verdict`'s PASS branch (line 2220) and category promotions (2237, 2249); the 0.05 `effectively_garbled` threshold must keep distinguishing partially- from fully-garbled docs. **Validation flags this target as internally contradictory / possibly already-landed — see below; re-derive before implementing.** |
| `src/pageindex_mcp/converters.py` | 2489-2539 (corrected from spec's 2511-2550) | Thread `expected_script` through `_recover_picture_results` | Add optional `expected_script: str \| None = None`; forward it to `_recover_picture_text` at line 2539. | Called from `pdf_to_markdown_docling` at line 3447 — `expected_script` must be threaded from that new parameter. |

**Not yet in scope — required before this zone can be considered fixed (see Validation Results):**
- `pdf_markdown_converters()` chain / callable invocation in `converters.py` (~3530/3532), which is the **primary first-pass conversion route** and currently stores `pdf_to_markdown_docling` as `Callable[[str], ...]`, invoked with only `pdf_path`.
- Subprocess timeout-isolation worker at `converters.py:2867`.
- RFC-027 D7 chunked large-PDF route, `_pdf_to_markdown_docling_chunked` (def at 2950, dispatched at 3206).
- Remote Docling path (`_remote_pdf_to_markdown`) used when `state.use_remote` is set — the CLAUDE.md-documented default profile is remote Scaleway Docling, so as scoped today this fix may show zero effect on the default profile.

#### Wiring checks

| Symbol | Must be called/imported by | Check type |
|---|---|---|
| `pdf_to_markdown_docling(expected_script=...)` | `src/pageindex_mcp/client.py` | call |
| `_text_layer_has_content(expected_script=...)` | `src/pageindex_mcp/converters.py` (same-module call) | call |
| `_document_level_text_fallback(expected_script=...)` | `src/pageindex_mcp/converters.py` (same-module call) | call |
| `_recover_picture_text(expected_script=...)` | `src/pageindex_mcp/converters.py` (same-module call) | call |
| `_recover_picture_results(expected_script=...)` | `src/pageindex_mcp/converters.py` (same-module call) | call |

**Gap flagged by validation:** the four `converters.py` rows above are same-module private-function calls, not imports — `must_be_imported_by` is the wrong framing and an import-based verifier would vacuously pass/fail; reframe as AST call-kwarg checks. No wiring check currently exists for the `pdf_markdown_converters` chain, `_pdf_to_markdown_docling_chunked`, or the subprocess worker — the three routes most likely to silently miss the fix.

#### Test requirements

| Test file | What to test | Assertion type |
|---|---|---|
| `tests/test_zone1_expected_script_threading.py` | `converters.py` garble-checking functions (`_text_layer_has_content`, `_document_level_text_fallback`, region garble check) use filename-derived `expected_script` when provided, not text-inferred. Case: Arabic PDF with Latin garbage text layer flagged garbled when `expected_script='Arab'` is threaded, but would pass if derived from text (`infer_script` → `'Latn'`). | contract |
| `tests/test_zone1_expected_script_threading.py` | All new `expected_script` parameters default to `None` and reproduce pre-change behavior when not passed. | regression |
| `tests/test_zone1_garble_wiring.py` | Extend existing AST wiring test: `pdf_to_markdown_docling` signature includes `expected_script`; `client.py` callsites (1316, 1621) pass `expected_script=` as a keyword. | wiring |
| `tests/test_zone1_garble_ratio_consistency.py` | *As specified, likely unpassable — see Validation Results.* Original intent: `_garble_ratio` must not diverge from `check_garble` for the same text/`expected_script`/context. **Revise to:** for text ≤ window size, `_garble_ratio` must equal `check_garble`'s boolean (already guaranteed by the `len <= window` branch); for text > window size, windows must use `GarbleContext.TREE_BULK` and the caller's `expected_script`. | contract |
| `tests/test_zone1_converters_garble_context.py` | All 3 `converters.py` `check_garble` callsites pass a `GarbleContext` value (`PAGE_TEXT_LAYER`, `DOCUMENT_FALLBACK`, `REGION`) and pass `expected_script` explicitly (not `None`) when the caller has it in scope. AST-based verification. | exhaustiveness |

#### Corpus validation

- **Affected documents:** warid-597, MOU, qarar-106, arabicSLA, cabinet_resolution_no_96, Haftpflicht, siyasat-hawkama, marsoom-13
- **Expected verdict direction:** improve
- **Spot-check count:** 8
- **Caveat from validation:** if the corpus is ingested under the default remote-Docling profile (`state.use_remote`), the expected improvement may not materialize at all, since the remote path bypasses `pdf_to_markdown_docling` entirely. Force local conversion for this validation run, or extend scope to the remote request path.

## Validation Results

**Overall quality: `needs_work`. Not approved for implementation as specified.**

7 issues surfaced against the single scored zone (Zone 1):

1. **[blocker]** Primary ingestion route never receives `expected_script`. The spec's callsites (`client.py:1316`/`1621`) are the force-OCR escalation paths, not the main conversion path — the main path is `client.py:1029` → `pdf_markdown_converters()` chain (`converters.py:3530`/`3532`), where `pdf_to_markdown_docling` is stored as `Callable[[str], ...]` and invoked with only `pdf_path`. All three `converters.py` garble sites fire on this primary route with text-inferred script, so the Arabic/Latin-garbage bypass remains unfixed for every first-pass conversion. *Suggested fix: add a code target updating the chain invocation/type annotation (e.g. `functools.partial` with `expected_script`) plus a wiring check for it.*
2. **[major]** Two more `pdf_to_markdown_docling` callsites are missed: the subprocess timeout-isolation worker (`converters.py:2867`) and the RFC-027 D7 chunked large-PDF route (`_pdf_to_markdown_docling_chunked`, def at 2950, dispatched at 3206 — no `expected_script` in its signature). Large PDFs and subprocess-routed conversions silently drop the parameter even after the fix. *Suggested fix: thread `expected_script` through the subprocess worker args and the chunked route, plus wiring checks.*
3. **[major]** Remote Docling path unaddressed: when `state.use_remote` is set (the project's default profile per CLAUDE.md is remote Scaleway Docling), both escalation callsites route through `_remote_pdf_to_markdown` instead, so `expected_script` never reaches converter-side garble checks. The corpus validation plan may show zero delta under the default profile. *Suggested fix: scope explicitly to local conversion and force the local converter for validation, or propagate `expected_script` through the remote service request.*
4. **[major]** The `_garble_ratio` code target (`helpers.py:1958-1976`) is internally contradictory and partly a no-op. RFC-033 D1 already landed: `_garble_ratio` uses `check_garble` with `GarbleContext.TREE_BULK` and `expected_script` is already threaded from its sole caller (`helpers.py:390`), so the fallback option is already true. The primary option (single-shot / node-based ratio) violates the spec's own constraint that the 0.05 `effectively_garbled` threshold must keep distinguishing partial from full garbling — a single-shot check only yields 0.0/1.0 — and `_garble_check_nodes` needs tree nodes while `_garble_ratio` receives flat text. *Suggested fix: drop or rewrite with one concrete design; if the windowed ratio is already correct, state no change is needed and remove the target.*
5. **[major]** `test_zone1_garble_ratio_consistency.py` asserts an invariant the windowed design cannot satisfy: for text > 2000 chars, `check_garble=True` on the bulk text with every 2000-char window individually below threshold (dilution) is a legitimate, non-buggy outcome — so "ratio cannot be 0.0 when check_garble is True" either forces the contradictory redesign in issue 4 or is unpassable. *Suggested fix: restate as in the revised test-requirements row above.*
6. **[major]** Strategy/code-target mismatch: the strategy promises removing the `_infer_script` override in `_garble_check_nodes` for nodes < 50 chars ("falls back to `page_script` anyway"), but no code target implements it, and current code (`helpers.py` ~1543: `inferred = _infer_script(text) if len(text) >= 50 else None`) already performs no override below 50 chars — and the sub-50 fallback is `expected_script`, not `page_script`. The described change is a no-op built on a misreading of QF3. *Suggested fix: delete this clause from the strategy, or add a real code target after re-reading `helpers.py:1537-1555`.*
7. **[minor]** Line inaccuracy: `_recover_picture_results` is defined at `converters.py:2489`, not "around 2511-2550" (2511 falls inside its docstring); the original `lines` field (2511-2539) excluded the signature line that must gain the new parameter. **Corrected to 2489-2539 in the code-targets table above.**
8. **[minor]** Wiring checks misuse `must_be_imported_by` for private same-module functions listed as "imported by" the module that defines them — these are same-module calls, not imports; `check_type: call` mitigates but an import-based verifier could vacuously pass/fail. No wiring check exists for the chain/chunked/subprocess routes flagged in issues 1-2, so the wiring gate as specified cannot catch the biggest gap. *Suggested fix: reframe as AST call-kwarg checks within `converters.py`, and add checks for the `pdf_markdown_converters` chain, `_pdf_to_markdown_docling_chunked`, and the subprocess worker forwarding `expected_script`.*

**Before implementation:** resolve blocker #1 and majors #2-#6 by extending the code-target list to cover the primary conversion chain, the chunked route, the subprocess worker, and (at minimum) scope the remote-Docling gap explicitly; re-derive or drop the `_garble_ratio` target; correct the ratio-consistency test to the revised invariant. Given the git-log evidence that Zones 2 and 5 may already be landed (commits `646cdc0`, `f37584e`), a `zone-delta-analysis` re-run against all 8 zones — not just Zone 1 — is recommended before committing further engineering time, so scores reflect actual landed state rather than the conservative no-delta 1.2x multiplier.
