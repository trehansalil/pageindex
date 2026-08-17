# Remediation Plan — 2026-08-17

**Audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-17_POST-FIX-4.md
**Zones:** 3 of 8 (top by priority)
**Waves:** 3

## Priority Scores

| Zone | Score | Severity | Bug Count | Proposal Status | Excluded |
|---|---|---|---|---|---|
| Zone 1: Garble Detection Hydra | 57.6 | critical | 12 | no_proposal | no |
| Zone 2: God Function Routing Cascade (client.py index()) | 52.8 | critical | 11 | no_proposal | no |
| Zone 5: OCR/Enrichment Signal Conflation | 32.4 | high | 9 | no_proposal | no |
| Zone 4: Threshold Calibration Feedback Loops | 28.8 | high | 8 | no_proposal | no |
| Zone 3: Verdict Persistence Split-Brain | 25.2 | high | 7 | no_proposal | no |
| Zone 6: Conversion Pipeline Stage Coupling (pdf_to_markdown_docling) | 25.2 | high | 7 | no_proposal | no |
| Zone 7: Registry/Persistence Consistency Gaps | 14.4 | medium | 6 | no_proposal | no |
| Zone 8: Dead/Uncommitted/Stale Code Divergence | 14.4 | medium | 6 | no_proposal | no |

Score formula: severity_weight × bug_count × proposal_multiplier (1.2 for no_proposal — no prior-run delta exists to classify wiring status). Only the top 3 zones (Zone 1, Zone 2, Zone 5) are in scope for this remediation plan; Zones 3, 4, 6, 7, 8 are scored for prioritization reference but not planned here.

## Wave Sequence

### Wave 1 — Zone 1: Garble Detection Hydra

Zone 1 has zero upstream dependencies and produces the foundational `check_garble()` unified API that both Zone 2 and Zone 5 consume. The current gap: the primary first-pass PDF conversion chain (`pdf_markdown_converters()`) never threads `expected_script` into `pdf_to_markdown_docling`, so all first-pass garble checks fall back to `infer_script(text)` — the OCR-escalation retry paths already pass `expected_script` correctly, but the primary path does not. Estimated effort: small (thread one kwarg through chain + converter + type annotation).

**Shared files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py`

**Note:** The wave rationale in the original plan data described a full garble-detection rewrite (deleting `_tree_is_garbled`/`_flat_text_is_garbled`, adding `check_garble`) — that work has already landed (commits 33cc1f5, 113c33a). The residual work for this wave is scoped narrowly to the `expected_script` threading gap above; the 2.75-day estimate in the original plan reflected the already-completed rewrite and is not applicable to the remaining scope.

### Wave 2 — Zone 2: God Function Routing Cascade (client.py index())

Zone 2 depends on Zone 1's unified garble API (recovery methods call `check_garble`). The decomposition of the 1409-line `index()` into `ExtractionState`, recovery methods, and persist helpers has already landed (commit 646cdc0). Residual work is a hardening pass: stale `first_defect`/`route` after `_reconvert_and_revalidate`, implicit if/elif Route dispatch instead of exhaustive match, VLM recovery logic embedded in `_persist_flat_result` instead of the recovery pipeline, and fragile tuple-destructuring in `ExtractionSnapshot.restore()`. Must precede Zone 5 because Zone 5's client.py changes (image path, enrichment promotion) target code this wave is restructuring. Estimated effort: medium — see Validation Results for blocking issues that must be resolved before implementation.

**Shared files:** `src/pageindex_mcp/client.py`, `src/pageindex_mcp/helpers.py`

### Wave 3 — Zone 5: OCR/Enrichment Signal Conflation

Zone 5 depends on both prior waves: Zone 1's unified garble path (so `OCR_ESCALATION_GARBLE` references a single `check_garble` call), and Zone 2's decomposed/hardened client.py (so image-route and enrichment-promotion changes land in clean methods, not a monolith or a mid-refactor target). The primary fix (OCR flag split, `primary_text` field, shared enrichment helper) has already landed (commit f37584e). Residual work: delete dead `_flat_block_text`, migrate 5 test files off it, make image-block presence visible to `content_class` computation, and add a digit/barcode noise ratio check to the `image_enrichment_promoted` branch (closes the warid-597-class bypass). Estimated effort: medium.

**Shared files:** `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py`, `src/pageindex_mcp/config.py`, `src/pageindex_mcp/helpers.py`

---

## Fix Specs

### Zone: Zone 1: Garble Detection Hydra (wave 1, priority 1)

**Mechanism to eliminate:** The primary PDF conversion chain (`pdf_markdown_converters()`) stores `pdf_to_markdown_docling` as `Callable[[str], ...]` and client.py invokes it at lines 1063 and 1079 with only `file_path`, never passing `expected_script`. Every first-pass local Docling conversion runs converters.py-internal garble checks (`_text_layer_has_content`, `_document_level_text_fallback`, region garble check) with `expected_script=None`, causing them to fall back to `infer_script(text)` on the text being checked. An Arabic PDF whose text layer contains Latin garbage infers `expected_script="Latn"`, bypassing the `latin_gibberish` prong entirely. The force-OCR-escalation retry paths (client.py:1315, 1621) already pass `expected_script` directly — the gap is specific to the primary first-pass path.

**Strategy:** Thread `expected_script` through the `pdf_markdown_converters()` chain by changing the chain callable type to accept an optional `expected_script` keyword argument, wrapping `pdf_to_markdown_docling` via the two callsites in `_convert_to_tree`, and updating chain invocation to pass `expected_script`. The pymupdf4llm route (`_pdf_to_markdown_no_pics`) has no internal garble checks, so make the parameter optional via `**kwargs` absorption. Drop the `_garble_ratio` target (already correct after RFC-033 D1). Document the remote Docling gap as out-of-scope (server-side limitation).

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/client.py` | 1060-1079 | Pass `expected_script` through the chain callable invocation for the primary first-pass PDF conversion | At lines 1063/1079 where `conv_fn` is invoked, add `expected_script=expected_script` when `'docling' in conv_name`: docling branch → `conv_fn(file_path, True, ocr_lang_override=detect_ocr_langs(filename), expected_script=expected_script)`; general branch → `conv_fn(file_path, expected_script=expected_script)` else `conv_fn(file_path)` | Must not break the pymupdf4llm route; must preserve chain iteration fallback semantics; `expected_script` already in scope (parameter at line 957) |
| `src/pageindex_mcp/converters.py` | 3496-3499 | Make `_pdf_to_markdown_no_pics` accept and ignore `expected_script` | Add `**kwargs` to `_pdf_to_markdown_no_pics(pdf_path: str, **kwargs)`, or an explicit `expected_script: str | None = None` param, ignored | Must still return `(markdown, [], {})`; no behavior change |
| `src/pageindex_mcp/converters.py` | 3502-3555 | Update `pdf_markdown_converters()` chain type annotation | Change return type to `Callable[..., tuple[...]]` or define a `PdfConverterFn` Protocol with the full `(pdf_path, *, expected_script=None, **kwargs)` signature | Must not break chain iteration callers; both converters must satisfy the type |
| `src/pageindex_mcp/client.py` | 1045-1058 | Document remote Docling gap (known limitation) | Add comments noting `_remote_pdf_to_markdown` does not forward `expected_script`; server-side garble checks use text-derived script; post-conversion garble detection already receives `expected_script` | Comment-only, no behavioral change |

**Wiring checks:**
- `pdf_to_markdown_docling(expected_script=...)` — call — must be imported/called by `src/pageindex_mcp/client.py`
- `_pdf_to_markdown_no_pics(expected_script=... or **kwargs)` — call — verify at the actual chain-consumption site (converters.py's own chain construction), not merely "defined in converters.py" (that check is vacuous per validation findings)
- `pdf_markdown_converters() chain invocation with expected_script` — call — must be present in `src/pageindex_mcp/client.py`

**Test requirements:**
- `tests/test_zone1_chain_expected_script.py` (wiring) — AST-parse client.py, confirm the chain-iteration `conv_fn` call site passes `expected_script` as a keyword; confirm `_pdf_to_markdown_no_pics` accepts it without error
- `tests/test_zone1_chain_expected_script.py` (contract) — mock inner `check_garble` calls; verify `expected_script='Arab'` passed through `pdf_to_markdown_docling` reaches `_text_layer_has_content`/`_document_level_text_fallback`/region check unchanged, not re-inferred via `infer_script(text)`
- `tests/test_zone1_chain_expected_script.py` (regression) — `_pdf_to_markdown_no_pics` with/without `expected_script` produces identical output
- `tests/test_zone1_garble_wiring.py` (wiring) — extend existing AST test to cover the primary chain path at lines 1063/1079, not just the escalation paths at 1315/1621

**Corpus validation:** affected documents — warid-597, MOU, qarar-106, arabicSLA, cabinet_resolution_no_96, Haftpflicht, siyasat-hawkama, marsoom-13. Expected verdict direction: improve. Spot-check count: 8.

**Estimated complexity:** small

---

### Zone: Zone 2: God Function Routing Cascade (client.py index()) (wave 2, priority 2)

**Mechanism to eliminate:** Recovery branches guard on stale `state.first_defect` (set once in `_convert_to_tree`, never refreshed after `_reconvert_and_revalidate` updates `ok`/`reason`/`gate_result`), so downstream recovery methods see outdated defect type. Route dispatch in the orchestrator uses implicit if/elif fallthrough rather than exhaustive match on `Route`, so `Route.PERSIST_FAIL` is handled by accident and adding a new Route value would silently produce wrong behavior. Recovery methods set `state.route = Route.FLAT` directly, bypassing `decide_route`. `_persist_flat_result` embeds VLM recovery logic that belongs in the recovery pipeline. `ExtractionSnapshot.restore()`'s tuple-destructuring revert pattern is fragile — adding a field silently breaks all callers without a type error.

**Strategy:** Hardening pass on the already-decomposed pipeline: (1) refresh `first_defect`/`route` after `_reconvert_and_revalidate` so downstream guards see post-recovery state; (2) replace implicit if/elif Route dispatch with an exhaustive match that raises on unhandled values; (3) extract flat-path VLM recovery from `_persist_flat_result` into a dedicated recovery method; (4) replace `ExtractionSnapshot.restore()` tuple-destructuring with named-field `apply_to()`; (5) centralize route overrides into `state.force_route()` with logging.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/helpers.py` | 176-200 | Add `recompute_defect_and_route` to `ExtractionState` | Read `gate_result.defect` (or `_defect_from_reason_str(reason)`), call `decide_route()`, set `route`; takes `flat_routing_enabled: bool` | `ExtractionState` remains a mutable `@dataclass`; existing field set unchanged. **See blocker below on `first_defect` semantics and route-forced interplay.** |
| `src/pageindex_mcp/client.py` | 921-950 | Call `state.recompute_defect_and_route()` at end of `_reconvert_and_revalidate` | After `state.original_gate_result = state.gate_result`, add the recompute call + update `state.total_chars` | Recovery-method guard order across all 7 recovery methods must remain unchanged; pre-retry snapshot's `first_defect` must not be affected |
| `src/pageindex_mcp/client.py` | 2112-2162 | Replace implicit if/elif Route dispatch with exhaustive match | `match state.route:` covering TREE (persist tree), FLAT (persist flat, explicit None-handling — no fallthrough), REJECT (raise `LowQualityTreeError`), PERSIST_FAIL (persist tree w/ FAIL verdict), `case _:` raises `AssertionError` | Per-route behavior must remain identical; **the `flat_garble_unrecovered` reject condition currently applies across routes via an or-clause and must not be narrowed to only the FLAT case — see blocker below** |
| `src/pageindex_mcp/client.py` | 1710-1790 | Extract VLM recovery from `_persist_flat_result` into `_recover_flat_garble_vlm` | New method guarded on `not state.ok and route==FLAT and flat_garble_unrecovered and ext=='.pdf' and settings.vlm_fallback` | **Sequencing is currently broken as specified — see blocker below; must be resolved before implementation** |
| `src/pageindex_mcp/helpers.py` | 107-173 | Replace `ExtractionSnapshot.restore()` with `apply_to(state)` | Named-field assignment for each snapshot field | `ExtractionSnapshot` remains frozen; `from_state` unchanged; all `restore()` callers updated; **field-coverage test must allowlist intentionally-uncovered fields (e.g. `defect`) — see minor issue below** |
| `src/pageindex_mcp/client.py` | 1398-1413 | Replace `pre_retry.restore()` tuple destructuring with `pre_retry.apply_to(state)` | Single call, then `state.recompute_defect_and_route(...)` to refresh routing post-revert | `tmp_md_path` rewrite logic after revert must remain |
| `src/pageindex_mcp/helpers.py` | 176-200 | Add `force_route(self, route, reason)` to `ExtractionState` | Capture `old = self.route` **before** assignment, set `self.route = route`, log `(old, route, reason)` | Lightweight; no side effects beyond logging + field set; **must define precedence vs. `recompute_defect_and_route` — see blocker below** |
| `src/pageindex_mcp/client.py` | 1495,1549,1565,1677,1707 | Replace direct `state.route = Route.FLAT` assignments with `state.force_route()` calls | One call per site with a distinguishing reason string (`rtl_flat_compare`, `vlm_tesseract_recovery`, `flat_prefer`, `landscape_reroute`) | Preceding `state.ok = False` assignments must remain — `force_route` only sets route |

**Wiring checks:**
- `ExtractionState.recompute_defect_and_route` — call — `src/pageindex_mcp/client.py`
- `ExtractionSnapshot.apply_to` — call — `src/pageindex_mcp/client.py`
- `ExtractionState.force_route` — call — `src/pageindex_mcp/client.py`
- `_recover_flat_garble_vlm` — call — `src/pageindex_mcp/client.py`, sequenced correctly relative to `_persist_flat_result` (see blocker)

**Test requirements:**
- `tests/test_zone2_route_dispatch_exhaustive.py` (exhaustiveness) — mock `validate_tree` per Route value (TREE/FLAT/REJECT/PERSIST_FAIL), assert correct behavior; assert a hypothetical new Route hits the default case
- `tests/test_zone2_defect_refresh.py` (contract) — post-`_reconvert_and_revalidate`, `first_defect`/`route` refresh correctly; post-revert via `apply_to`, they match pre-retry values — **must be re-specified once the `first_defect` write-once-vs-current-value semantics conflict (below) is resolved**
- `tests/test_zone2_extraction_snapshot.py` (contract) — `apply_to` sets all named fields; field-coverage assertion with explicit allowlist for `defect`
- `tests/test_zone2_persist_flat_no_recovery.py` (contract) — `_persist_flat_result` no longer calls `vlm_extract_markdown`; `_recover_flat_garble_vlm` is invoked from the orchestrator on the correct signal (post-resequencing)
- `tests/test_zone2_force_route.py` (contract) — `force_route` sets route, logs `route_override` with old→new transition and reason; add a forced-route-survives-reconvert regression case
- `tests/test_zone2_persist_fail_e2e.py` (regression) — `EMPTY_NODE_CONTAMINATION` → `Route.PERSIST_FAIL` persists via `save_doc` with FAIL verdict, no exception, no flat reroute

**Corpus validation:** affected documents — warid-597 (flat garble VLM recovery), marsoom-13 (Arabic OCR escalation + flat fallback), arabicSLA (RTL reversal + flat compare), cabinet_resolution_no_96 (PERSIST_FAIL route), federal_decree_law_no_33 (content density gate, PERSIST_FAIL), Penal Code family (node_count_low → OCR escalation eligibility), MOU (bidi degraded, tree persist CAP_MARGINAL), Haftpflicht (garble detection, tree/flat boundary), image_pie_chart (standalone image, image-dominant OCR). Expected verdict direction: stable. Spot-check count: 9.

**Estimated complexity:** medium (contingent on resolving the blockers below before implementation begins)

---

### Zone: Zone 5: OCR/Enrichment Signal Conflation (wave 3, priority 3)

**Mechanism to eliminate:** A single `OCR_ESCALATION` boolean conflated page-level garble retry with per-picture crop+OCR enrichment, so toggling one disabled the other. The enrichment promotion path in `classify_verdict` checked char volume using `_flat_block_text`, which included enrichment metadata (ocr_text/description from image blocks), inflating counts and letting barcode/digit-noise docs (warid-597) pass floor checks. The standalone-image path had its own inline enrichment logic separate from the PDF path, losing `splice_figure_markers`/`_enrich_image_blocks`. `content_class` computation in `route_and_extract_flat` counts only table/kv/prose signals, making image blocks invisible to classification.

**Strategy:** The primary fix (OCR flag split, `primary_text` field, shared enrichment helper) has already landed (commit f37584e). Remaining work: (D) delete dead `_flat_block_text`, migrate 5 test-file callers to `_flat_block_primary_text`; add image-block visibility to `content_class` computation in `route_and_extract_flat`; add a digit/barcode noise ratio check to the `image_enrichment_promoted` branch so barcode noise does not pass the char floor even via `primary_text`.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/helpers.py` | `_flat_block_text` function body (verify actual location — `_flat_block_primary_text` at 3491, `_flat_block_text` at 3507, `_flat_search_text` at 3533 per validation; **do not use stale range 3512-3535, which cuts mid-body and clips `_flat_search_text`**) | Delete dead `_flat_block_text` | Delete by symbol boundary (whole function, 3507 through the line before 3533); verify `_flat_search_text` intact after edit | `_flat_search_text` must remain byte-identical — it serves the search-index path and legitimately includes enrichment metadata |
| `src/pageindex_mcp/helpers.py` | 3432-3478 | Make image-block presence visible to `content_class` computation | **Pick one design, not both**: either return `image_block_count` (or attach to blocks metadata) with a named downstream consumer, or drop this target — the "add signal.add('image'), no-op on content_class" framing from the original spec is rejected as underspecified | `content_class` string values (flat_prose/flat_mixed/flat_table/flat_kv) must not change; the {table,kv,prose} intersection at line 3478 stays as-is |
| `src/pageindex_mcp/helpers.py` | 2196-2201 | Add digit/barcode noise ratio check to `image_enrichment_promoted` branch | After computing `_promoted_text`/`total_chars`, compute digit ratio via one named helper (pick `ocr_noise_ratio` or a new `_digit_ratio` — not both as an unresolved "or"); if ratio exceeds threshold (>= 0.6), return MARGINAL with reason `image_enrichment_promoted_digit_noise` | Threshold >= 0.6 to avoid false positives on legitimate numeric/financial docs; garble check at line 2200 remains secondary filter; add a wiring check for the chosen helper/reason and a negative test on a numeric-but-legitimate document below threshold |
| `tests/test_rfc022_b3.py` | 18-86 | Migrate imports off `_flat_block_text` | Replace with `_flat_block_primary_text` | Non-image block semantics unchanged; image-enrichment-text-inclusion tests move to `_flat_search_text` tests or are deleted |
| `tests/test_rfc024_d6.py` | 22-67 | Migrate imports off `_flat_block_text` | Straight rename to `_flat_block_primary_text` (identical behavior for table row_records) | Must match production usage at client.py:1835 |
| `tests/test_rfc023_d5.py` | 14-25 | Migrate imports off `_flat_block_text` | Update import + block-to-structure conversion only | Tree depth/node-count test logic unaffected |
| `tests/test_rfc027_d0.py` | 1-81 | Update tests for `_flat_block_text` deletion | Rewrite the divergence test (lines 46-70) to compare `_flat_block_primary_text` vs `_flat_search_text` instead of the deleted function | Search-indexing enrichment-inclusion contract must remain tested via `_flat_search_text` |

**Wiring checks:**
- `_flat_block_primary_text` — call — `src/pageindex_mcp/client.py`
- `_flat_search_text` — call — verify at the actual search-index builder consumption site, not merely "defined in helpers.py" (vacuous self-module check per validation findings)
- `OCR_ESCALATION_GARBLE` — call — `src/pageindex_mcp/client.py`
- `OCR_ESCALATION_PER_PICTURE` — call — `src/pageindex_mcp/client.py`, `src/pageindex_mcp/converters.py`
- `decide_ocr_mode` — call — `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py`
- `_apply_picture_enrichment` — call — `src/pageindex_mcp/client.py`
- `OcrMode` — import — `src/pageindex_mcp/converters.py`, `src/pageindex_mcp/client.py`
- digit-noise helper + `image_enrichment_promoted_digit_noise` reason string — call — `src/pageindex_mcp/helpers.py` (new; not in original spec, added per validation finding)

**Test requirements:**
- `tests/test_zone5_primary_text.py` (regression) — doc with high enrichment ratio and `primary_text` >60% digit chars returns MARGINAL/`image_enrichment_promoted_digit_noise`, not PASS
- `tests/test_zone5_enrichment_unify.py` (contract) — `route_and_extract_flat` on image-marker-only input surfaces image presence per the chosen concrete design; `content_class` stays `flat_prose`
- `tests/test_zone5_primary_text.py` (contract) — `_flat_block_text` is no longer importable from helpers (ImportError)
- `tests/test_rfc027_d0.py` (contract) — `_flat_search_text` includes enrichment metadata (ocr_text + description) from image blocks
- `tests/test_zone5_ocr_split.py` (exhaustiveness) — `OCR_ESCALATION_GARBLE=1` + `OCR_ESCALATION_PER_PICTURE=0` allows garble retry while disabling per-picture crop+OCR, end-to-end through `decide_ocr_mode`
- new negative test — numeric-but-legitimate document below the 0.6 digit-ratio threshold does not trigger `image_enrichment_promoted_digit_noise`

**Corpus validation:** affected documents — warid-597, marsoom-13, MOU_MOHRE, arabicSLA. Expected verdict direction: improve. Spot-check count: 4.

**Estimated complexity:** medium

---

## Validation Results

**Overall quality: needs_work — plan is NOT approved as specified. The following issues must be resolved before implementation, ordered by severity.**

### Blockers

1. **Zone 2 — `_recover_flat_garble_vlm` is dead on arrival as sequenced.** Its guard requires `state.flat_garble_unrecovered=True`, but that flag is only set *inside* `_persist_flat_result` (after `route_and_extract_flat` + `splice_figure_markers` produce the final `flat_md`), while the spec places the recovery call before `_persist_flat_result` ever runs (after `_recover_landscape_reroute`, before Route dispatch). The spec also claims the VLM updates `state.md_content`, but currently the VLM operates on `flat_md`, a local variable inside `_persist_flat_result`, not on state. As specified, the flat-path VLM recovery (warid-597) never fires — the opposite of the "improve" corpus expectation.
   **Required fix before implementation:** resequence so `_persist_flat_result` returns a garble signal (`None` + `flat_garble_unrecovered=True`), the orchestrator calls `_recover_flat_garble_vlm` on that outcome, then re-invokes `_persist_flat_result`; or have the recovery method receive/regenerate `flat_md` explicitly. State precisely which markdown artifact the VLM output replaces.

### Major issues

2. **Zone 2 — `recompute_defect_and_route` silently redefines `first_defect` semantics.** Currently documented as write-once ("first defect observed" — `ExtractionSnapshot` docstring at helpers.py:112 explicitly excludes it from revert). The spec's refresh turns it into "current defect," but downstream consumers (`check_garble(original_defect=state.first_defect)` in `_persist_flat_result` ~1745, `_reject_reason = state.first_defect.value` in `index()` ~2127) depend on the original write-once meaning and are never audited by the spec.
   **Required fix:** either add a separate `current_defect` field and leave `first_defect` write-once, or enumerate and regression-test every `first_defect` consumer under the new semantics.

3. **Zone 2 — `force_route` vs. `recompute_defect_and_route` precedence is unspecified and self-defeating.** A recovery method forces `Route.FLAT` (e.g. `_recover_rtl_flat_compare`), then any later recovery method calling `_reconvert_and_revalidate` (e.g. `_recover_vlm_fallback`, `_recover_flat_prefer`) triggers `recompute_defect_and_route`, which recomputes route via `decide_route` and clobbers the forced FLAT back to policy-derived.
   **Required fix:** add a `route_forced` flag set by `force_route`; `recompute_defect_and_route` must skip route recomputation (or only recompute defect) when set, with a test covering forced-route-survives-reconvert.

4. **Zone 5 — stated deletion line range for `_flat_block_text` is wrong and would corrupt `_flat_search_text`.** Actual layout: `_flat_block_primary_text` at helpers.py:3491, `_flat_block_text` def at 3507, `_flat_search_text` def at 3533 (not "3538+" as originally stated). Deleting lines "3512-3535" as specified cuts `_flat_block_text` mid-body *and* removes `_flat_search_text`'s def line/docstring — the exact function the spec's own constraint says must remain unchanged.
   **Required fix:** delete by symbol boundary, not stale line range; verify `_flat_search_text` intact post-edit (reflected in the Fix Specs table above).

### Minor issues

5. **Zone 2** — the `flat_garble_unrecovered` reject condition currently applies via an or-clause across any route (client.py:2120-2122: `route in (REJECT, FLAT) or state.flat_garble_unrecovered`), but the spec's match-statement constraint narrows it to the FLAT case body only — a behavioral change. Also, "persist flat then fall through to reject on None" cannot exist as fallthrough inside `match`/`case`; the None-handling must be written explicitly in the FLAT case.
6. **Zone 2** — the `apply_to` field-coverage test ("adding a field without updating `apply_to` fails a test") is inconsistent with the design itself: `ExtractionSnapshot` already carries a `defect` field that `apply_to` intentionally omits. Needs an explicit allowlist (`{'defect'}`).
7. **Zone 2** — `force_route`'s log call as originally described logs the *new* route for both old and new positions if assignment happens before logging, losing the transition. Fixed in the Code targets table above: capture `old = self.route` before assignment.
8. **Zone 5** — the `route_and_extract_flat` "image signal" target was a self-admitted no-op with an unresolved "alternatively..." fork and a conditional test requirement. Resolved in the Code targets table above by requiring one concrete design be chosen (return `image_block_count` with a named consumer, or drop the target).
9. **Zone 5** — the digit-noise mechanism had no wiring check and no false-positive test on legitimate numeric documents. Added to Wiring checks and Test requirements above.
10. **Zone 1** — mechanism description cited stale line numbers (`_text_layer_has_content` at 1652 vs. actual 1636; `_document_level_text_fallback` at 1751 vs. actual 1699) — code_targets themselves were correct. Prefer symbol names over line numbers going forward.
11. **Zone 1 / Zone 5** — two wiring checks were vacuous self-module checks (`_pdf_to_markdown_no_pics` "must be imported by converters.py" where it's defined; `_flat_search_text` "must be imported by helpers.py" where it's defined and only called). Replaced above with checks against the actual cross-module/consumption site.
12. **Wave rationales** (original plan data) described already-landed work as upcoming wave content: Wave 1 described the full garble-detection rewrite (landed in 33cc1f5/113c33a) and Wave 2 described the `index()` decomposition (landed in 646cdc0). This document's Wave Sequence section above has been rewritten to describe only residual work, with effort estimates adjusted down accordingly (Wave 1: small, not 2.75 days; Wave 2/3: medium, not 3-4/3 days as originally stated for full rewrites).

**Net assessment:** proceed with Wave 1 (Zone 1) as specified — no blockers. Do not begin Wave 2 (Zone 2) implementation until blockers #1-#3 are resolved in a revised spec. Wave 3 (Zone 5) code targets in this document already incorporate the required fixes for issues #4, #8, #9 and can proceed once Wave 2 lands.
