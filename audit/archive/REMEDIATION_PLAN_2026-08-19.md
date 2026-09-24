# Remediation Plan — 2026-08-19

**Audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-19_POST-FIX-10.md
**Zones:** 7 of 7 (top by priority)
**Waves:** 3
**Overall validation quality:** `needs_work` — **NOT APPROVED**. 17 issues found (4 blocker, 8 major, 5 minor) — see [Validation Results](#validation-results). This plan must not be executed as-written; blockers must be resolved and specs corrected before any wave starts.

---

## Priority Scores

Scoring formula: `severity_weight × bug_count × status_multiplier × regression_boost`. Status multipliers: `no_proposal=1.2`, `partially_implemented=1.5`, `implemented_and_wired=0.3`. Regression boost `×1.3` applied to zones whose delta shows new findings after a prior "fix" landed.

| Rank | Zone | Score | Severity | Bugs | Proposal Status | Excluded |
|---|---|---|---|---|---|---|
| 1 | Zone 2: Picture/OCR Enrichment and Page-Level Escalation Conflation | 57.6 | critical | 12 | no_proposal | no |
| 2 | Zone 5: Config Snapshot Freeze Drift and Incomplete Wiring Enforcement | 46.8 | high | 8 | partially_implemented (regressed) | no |
| 3 | Zone 6: Cross-Process Error Classification Boundary | 33.6 | critical | 7 | no_proposal | no |
| 4 | Zone 7: Mutable ExtractionState Recovery Path Ordering | 25.2 | high | 7 | no_proposal | no |
| 5 | Zone 1: GATE_TABLE to Recovery Dispatch Reason-Code Coupling | 18.72 | critical | 12 | implemented_and_wired (regressed) | no |
| 6 | Zone 3: Garble Detection Heuristic Patchwork | 14.4 | critical | 12 | implemented_and_wired (stalled) | no |
| 7 | Zone 4: Verdict Threshold Oscillation and Hysteresis Failure | 10.53 | high | 9 | implemented_and_wired (regressed) | no |

**Notes on ranking vs. wave order:** priority score (fix urgency) and wave assignment (safe execution order given file/data dependencies) diverge deliberately — Zone 2 scores highest but lands in wave 3 because it structurally depends on Zone 1 and Zone 7 landing first. Zone 5 and Zone 6 score lower than Zone 2 but lead wave 1 because they are foundational/isolated.

---

## Wave Sequence

### Wave 1 — Foundation & infrastructure (parallel-safe)
**Zones:** Zone 6 (Cross-Process Error Classification Boundary) · Zone 5 (Config Snapshot Freeze Drift and Incomplete Wiring Enforcement) · Zone 7 (Mutable ExtractionState Recovery Path Ordering)

**Shared files:** `src/pageindex_mcp/helpers.py`

**Rationale:** Zone 6 (`worker.py`, `job_status.py`) is fully isolated with zero file overlap against any other zone. Zone 5 (`config.py` primary, `helpers.py:validate_feature_wirings` ~line 2092, `storage.py`) and Zone 7 (`helpers.py:RecoveryOutcome` ~line 175, `client.py`, `converters.py`) share `helpers.py` but their edit targets are 2000+ lines apart, making merge conflicts negligible. Zone 7 produces the `RecoveryOutcome` dataclass consumed downstream by Zones 1, 2, and 3 via `_recover_ocr_retry`. Zone 5 produces the config snapshot and wiring enforcement consumed by `worker.process_document_job` and `client.index`. Zone 6 fixes the error classification boundary in the isolated worker/job_status subsystem. All three are producers with no mutual data dependencies.

### Wave 2 — Garble-and-gate coordination (parallel-safe, contingent on fix below)
**Zones:** Zone 3 (Garble Detection Heuristic Patchwork) · Zone 1 (GATE_TABLE to Recovery Dispatch Reason-Code Coupling)

**Shared files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/client.py`

**Rationale:** Zone 3 (`check_garble`, `garble_prongs`, `_infer_script` in `helpers.py` mid-file; `normalize_for_garble` in `script.py`; `converters.py`) must land before Zone 4 since `compute_verdict` calls `check_garble`. Zone 1 (`GateSpec` at `helpers.py:262`; recovery dispatch in `client.py:_recover_ocr_retry`) must land before Zone 2 since gate reason codes drive OCR mode selection. Zones 3 and 1 share `helpers.py` and `client.py` but edit disjoint symbol sets. Both depend on Wave 1's `RecoveryOutcome` (Zone 7) being stable.

> **⚠️ Sequencing defect flagged by validation (blocker #4):** Zone 1's own `depends_on` names "Zone 3: Garble Detection Heuristic Patchwork" while both are scheduled in the *same* wave (parallel). A same-wave dependency is not a valid ordering — either Zone 3 must be pulled into an earlier sub-step within wave 2 (Zone 3 lands first, Zone 1 second, sequential not parallel) or the `depends_on` edge must be dropped if false. Given the specs describe a real data dependency (garble gates feeding recovery eligibility), **treat wave 2 as sequential: Zone 3 → Zone 1**, not parallel, until re-validated.

### Wave 3 — Consumers (parallel-safe on paper, blocked in practice — see below)
**Zones:** Zone 2 (Picture/OCR Enrichment and Page-Level Escalation Conflation) · Zone 4 (Verdict Threshold Oscillation and Hysteresis Failure)

**Shared files:** none

**Rationale:** Zone 2 (`picture_plane.py`, `converters.py`, `client.py`) depends on Zone 1's gate dispatch fixes (wave 2) to correctly route OCR mode decisions, and on Zone 7's `RecoveryOutcome` (wave 1) for the recovery path structure. Zone 4 (`compute_verdict`/`classify_verdict` in `helpers.py:2486-2711`, `find_prior_verdict`/`snapshot_prior_verdicts` in `storage.py`) depends on Zone 3's `check_garble` fixes (wave 2) since `compute_verdict` directly calls `check_garble`. These two zones have zero shared files, making them safe to parallelize with no merge conflict risk — **once their upstream specs are corrected** (see blockers #1, #2, #3 below: as currently written, Zone 2 targets code that Zone 1 deletes, and Zone 4 targets code that Zone 5 deletes).

---

## Fix Specs

### Zone: Zone 2 — Picture/OCR Enrichment and Page-Level Escalation Conflation (wave 3, priority 1)

**⚠ Spec requires correction before implementation** — see [Validation Results](#validation-results) blocker #3.

**Mechanism to eliminate:** Two competing OCR subsystems (page-level escalation in `client.py` via `_recover_ocr_retry`, and per-picture enrichment in `converters.py` via `_recover_picture_results`) make independent decisions at two temporal points using the same `decide_ocr_mode` function (`client.py:1075` pre-conversion with `has_image_markers=False` always, `converters.py:2630` post-conversion checking `_IMAGE_MARKER` in `md`). Each subsystem applies individually-reasonable filters (>60% page-coverage skip, clip-text >20 chars probe, forced-OCR PictureItem reclassification) that combine with the other subsystem's gates to produce emergent zero-output states neither subsystem detects. The re-entry guard (`force_full_page_ocr_applied` parameter at `converters.py:2593`, default `False`) is never set to `True` by any caller after `_recover_ocr_retry` performs full-page OCR, so per-picture OCR can duplicate or conflict with already-applied page-level OCR. Forced OCR reclassifies PictureItems as TextItems producing 0 PictureResults, which breaks `splice_figure_markers` count alignment and causes tree-to-flat collapse.

**Strategy:** Replace the two competing OCR subsystems with a single-writer `OcrDecision` frozen dataclass produced once by a unified `decide_ocr_strategy` function that takes complete document state (text-layer presence, garble status, coverage metrics, image-marker count) and emits a sealed instruction (exactly one of: no-OCR, full-page-OCR, per-picture-OCR) along with the language list and a `full_page_already_applied` flag. Collapse both call sites into a single decision point invoked after the primary converter returns when all inputs are known, eliminating the temporal ordering dependency. Thread `full_page_already_applied` through `ExtractionState` so `_recover_ocr_retry` stamps it `True` after successful retry, and `_recover_picture_results` reads it to short-circuit. Phase the migration: (A) additive `OcrDecision` dataclass + `decide_ocr_strategy` wrapper, zero behavior change; (B) `client.py` site replacement + `ExtractionState` threading; (C) `converters.py` call site replacement + `force_full_page_ocr_applied` wiring; (D) cleanup — delete legacy `decide_ocr_mode` and scattered config-flag reads.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/picture_plane.py` | 27-32, 326-348 | Add `OcrDecision` frozen dataclass and `decide_ocr_strategy` unified decision function; retain `decide_ocr_mode` as thin delegation wrapper during migration | `@dataclass(frozen=True) OcrDecision` with fields `mode`, `langs`, `full_page_already_applied` (default False), `garble_status` (default False), `has_image_markers` (default False), `coverage_metrics` (default None). `decide_ocr_strategy(*, ocr_escalation_enabled, has_image_markers, force_full_page=False, garble_status=False, text_layer_present=True, langs=()) -> OcrDecision` encodes all three flag checks in one place. Rewrite `decide_ocr_mode` to delegate. Net ~+25 lines. | `decide_ocr_mode` must remain callable with its existing 3-arg signature until all call sites migrate. `OcrMode` enum, `PictureGateConfig`, `_classify_region`, `bind_markers`, `SkipReason` must not change. |
| `src/pageindex_mcp/helpers.py` | 204-237 | Add `full_page_already_applied` field to `ExtractionState` dataclass | Add `full_page_already_applied: bool = False` after `flat_garble_unrecovered` (line 232). Net +2 lines. | `ExtractionState` stays mutable; existing fields/defaults preserved. `RecoveryOutcome` and `OcrRetryReason` not touched by this zone. **Note (validation minor issue):** Zone 7 lands first in wave 1 and also inserts a field after `flat_garble_unrecovered` — anchor this edit to the field name, not the line number, since the line will have drifted. |
| `src/pageindex_mcp/client.py` | 1075-1082 | Replace pre-conversion `decide_ocr_mode` call (site 1) with post-conversion `OcrDecision` production via `decide_ocr_strategy`; move decision point after converter returns so `has_image_markers` reflects actual markdown content | Move the `ocr_mode` computation from before the converter loop to after it completes (after `md_content` is assigned). Call `decide_ocr_strategy` with `has_image_markers=('<!-- image -->' in md_content)`, `force_full_page=(inspector_force_ocr or (state.pre_garbled and PRE_GARBLE_FORCE_OCR_ENABLED))`, `garble_status=state.pre_garbled`. Store as `state.ocr_decision`. Keep a pre-loop `force_full_page_initial` boolean for the initial extraction. Net ~-4 lines. | Converter dispatch loop must continue receiving the `force_full_page` signal for initial extraction; remote vs. local Docling branching preserved; `pic_results` assignment unchanged. |
| `src/pageindex_mcp/client.py` | `_recover_ocr_retry` — **line anchor invalid, see correction below** | Set `state.full_page_already_applied = True` after successful full-page OCR re-extraction | **AS WRITTEN**, targets "unified OCR dispatch block (line 1449)" inside the monolithic `_recover_ocr_retry` (lines 1328-1570) and constrains "the three `OcrRetryReason` branches (GARBLE, LOW_CONTENT, IMAGE_DOMINANT) must retain their independent flag gates." **This is unimplementable as written**: Zone 1 (its own declared wave-2 dependency) deletes `OcrRetryReason` entirely and splits `_recover_ocr_retry` into `_recover_garble_ocr` / `_recover_low_content_ocr` / `_recover_image_dominant_ocr` behind a shared `_execute_ocr_retry` helper, driven by a `GateSpec.recovery_fns` loop replacing `_recovery_dispatch`. **Corrected target:** stamp `state.full_page_already_applied = True` once, inside the shared `_execute_ocr_retry` helper (Zone 1's construct), after the OCR dispatch block and before the keep-best heuristic — not per-branch, and with no reference to `OcrRetryReason`. | Must fire exactly once regardless of which of the three split recovery methods called `_execute_ocr_retry`. Keep-best heuristic and `RecoveryOutcome.apply` semantics (Zone 7) must not be modified by this edit. |
| `src/pageindex_mcp/converters.py` | 2586-2666 | Confirm the `force_full_page_ocr_applied` re-entry guard (already exists at line 2593, guard at 2626 already returns `[]`) — no change needed to guard logic itself; fix is at the call site | No change needed in this function beyond verifying the guard remains intact through migration. | `decide_ocr_mode` call at line 2630 must remain functional during migration. Dense `PictureResult` list contract and `body_for_containment` snapshot preserved. |
| `src/pageindex_mcp/converters.py` | 3595-3663 | Thread `force_full_page_ocr_applied` through `_fallback_and_recover_pictures` to `_recover_picture_results`, closing the unwired re-entry guard | Add `force_full_page_ocr_applied: bool = False` keyword param to `_fallback_and_recover_pictures` (after `heading_pages`); pass through to `_recover_picture_results` at the call site. Update caller `pdf_to_markdown_docling` to pass `force_full_page_ocr_applied=force_full_page_ocr`. Net ~+4 lines. | Containment snapshot (`body_for_containment = pre_fallback_md`) must remain before `_document_level_text_fallback` runs. Landscape-fallback `PictureResult` append and `stage_records` provenance entry unaffected. |
| `src/pageindex_mcp/converters.py` | **line anchor invalid — see correction below** | Pass `force_full_page_ocr` through `pdf_to_markdown_docling` to `_fallback_and_recover_pictures` | **AS WRITTEN**, anchored at "3540-3592" as if that is the function definition span. **Validation minor issue #8:** `pdf_to_markdown_docling` is actually defined at `converters.py:3270`; 3540-3592 is only the function's tail (where the `_fallback_and_recover_pictures` call site at ~3581 lives). **Corrected anchor:** the call-site symbol `_fallback_and_recover_pictures(` inside `pdf_to_markdown_docling`, not a fixed line span. | `pdf_to_markdown_docling`'s existing signature and return type (`tuple[str, list[PictureResult], dict]`) must not change. `_pre_fallback_stages` pipeline untouched. |

**Wiring checks:**

| Symbol | Consumers required | Check type | Correction needed |
|---|---|---|---|
| `OcrDecision` | `src/pageindex_mcp/client.py` | import | — |
| `decide_ocr_strategy` | `src/pageindex_mcp/client.py` | call | — |
| `ExtractionState.full_page_already_applied` | `src/pageindex_mcp/client.py` | dispatch | — |
| `force_full_page_ocr_applied` | `src/pageindex_mcp/converters.py` | call | — |

**Test requirements:**
- `tests/test_zone2_ocr_decision.py` — `OcrDecision` contract exhaustiveness: frozen, typed fields, `decide_ocr_strategy` returns correct mode per input combo; `decide_ocr_mode` backward-compat delegation matches direct calls.
- `tests/test_zone2_ocr_decision.py` — mutual exclusion: exactly one of NONE/FULL_PAGE/PER_PICTURE returned; FULL_PAGE always wins over PER_PICTURE when `force_full_page=True`.
- `tests/test_zone2_reentry_guard_wiring.py` — AST-verified wiring: `force_full_page_ocr_applied` threaded from `pdf_to_markdown_docling` → `_fallback_and_recover_pictures` → `_recover_picture_results`; `state.full_page_already_applied = True` set inside the shared OCR-dispatch helper (post-Zone-1 surface) before the keep-best heuristic.
- `tests/test_zone2_ocr_recovery.py` — regression: existing `TestPerPictureReentryGuard` tests pass; new: full-page-applied flag on `ExtractionState` causes subsequent `_recover_picture_results` call to return `[]`.
- `tests/test_zone2_dual_decision_elimination.py` — AST-verified: only one `decide_ocr_mode`/`decide_ocr_strategy` call site in `client.py` (post-conversion only); `converters.py`'s independent per-picture gating call remains (correct by design).
- `tests/test_zone2_picture_plane.py` — regression: `OcrMode`, `SkipReason`, `PictureGateConfig`, `_classify_region`, `bind_markers` contracts unaffected.
- `tests/test_zone2_integration_zero_output.py` — integration: scanned PDF, >60% coverage, header/footer-only text layer, forced OCR reclassifying PictureItems → 0 PictureResults; after fix `full_page_already_applied=True` prevents redundant per-picture OCR and `splice_figure_markers` receives consistent `pic_results`.

**Corpus validation:** doc_3, doc_9, doc_7, doc_17, doc_20, doc_21 (Domestic Workers), Human-Rights. Expected direction: **improve**. Spot-check count: 7.

**Estimated complexity:** large. **Severity:** critical.

---

### Zone: Zone 5 — Config Snapshot Freeze Drift and Incomplete Wiring Enforcement (wave 1, priority 2)

**⚠ Spec requires correction before implementation** — see [Validation Results](#validation-results) blocker #2, major issue on wiring_checks.

**Mechanism to eliminate:** Three competing config-read sites produce temporal freeze drift: (1) `effective_config_snapshot` (`config.py:296-357`) rereads 25+ env vars fresh from `os.environ` on every call; (2) `VerdictThresholds.from_env` (`helpers.py:400-413`) reads env vars once on first `classify_verdict` call, caches forever via `_verdict_thresholds_cache`; (3) module-level constants in `helpers.py` (lines 1584, 1596, 1599, 1471, 1472, 3976) freeze at import time. The same env var read by the pipeline at one frozen value can diverge from what the sidecar audit record reports if the env changed mid-process. `validate_feature_wirings` (`helpers.py:2092-2187`) is only reachable via `atexit.register` — neither `server.py` nor `worker.py` call it at startup, so wiring gaps are only detected at process exit (if ever). `FEATURE_WIRINGS` covers only 4 cross-module features and never checks `GATES` list-order consistency or dual `RtlDecision` computation sites. `storage.delete_doc` has zero production entrypoints, making the HR2 right-to-erasure cascade (CLAUDE.md Hard Rule 2) unreachable.

**Strategy:** Consolidate all pipeline-behavior env-var reads into a single frozen `PipelineConfig` dataclass instantiated once at module load. Delete `_verdict_thresholds_cache`, `_get_verdict_thresholds`, `reset_verdict_thresholds`, and all scattered module-level `os.environ` reads. Rewrite `effective_config_snapshot` as `dataclasses.asdict(pipeline_config)`. Wire `validate_feature_wirings` into `server.py` lifespan and `worker.py` startup (removing `atexit` registration). Extend `FEATURE_WIRINGS` to cover `GATES` list and dual `RtlDecision` sites. Expose `storage.delete_doc` via an MCP tool endpoint to close the HR2 gap.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/config.py` | 296-357 | Create `PipelineConfig` frozen dataclass absorbing all 25+ behavior fields from `effective_config_snapshot`, plus threshold fields from `VerdictThresholds.from_env` and module-level frozen constants from `helpers.py` | `@dataclass(frozen=True) PipelineConfig` with classmethod `from_env()`. Module-level singleton `pipeline_config = PipelineConfig.from_env()`. Rewrite `effective_config_snapshot()` as one-liner. **Corrected assertion direction (was inverted — see blocker #2):** import-time assertion `pass_max_leaf_ratio <= leaf_split_ratio` (a document must not be rejected for concentration while the splitter that could fix it has a tighter/higher threshold). | Defaults must be byte-identical to current defaults. `Settings` dataclass untouched — `PipelineConfig` covers only pipeline-behavior flags, not infra (MinIO/Redis/Postgres). |
| `src/pageindex_mcp/helpers.py` | 400-437, 1471-1472, 1584-1604, 2229, 2530, 3976 | Delete `_verdict_thresholds_cache`, `_get_verdict_thresholds()`, `reset_verdict_thresholds()`, and module-level frozen constants; replace all reads with `pipeline_config.field_name` | Import `pipeline_config` from `.config`. Replace `_get_verdict_thresholds()` calls with `pipeline_config` access. `VerdictThresholds.from_env` becomes `VerdictThresholds.from_config(pipeline_config)` for the typed subset `classify_verdict` needs. Provide `reset_pipeline_config()` for test fixtures (replaces `reset_verdict_thresholds`). | All 238+ existing tests must still pass. `VerdictThresholds` remains a typed subset view — do not force every gate function to accept a full `PipelineConfig`. |
| `src/pageindex_mcp/helpers.py` | 2190-2195 | Remove `atexit.register(validate_feature_wirings)`; keep function exported for explicit invocation | Delete the `atexit` import and registration. | `validate_feature_wirings` must remain importable by `test_zone8_feature_wiring.py`. |
| `src/pageindex_mcp/helpers.py` | 2054-2089 | Extend `FEATURE_WIRINGS` to cover `GATES` list consistency and dual `RtlDecision` sites | Append two `FeatureWiring` entries: `rtl_decision` (producer `pageindex_mcp.script.decide_rtl`, consumers `helpers`+`client`) and `gate_recovery_dispatch` (producer `pageindex_mcp.helpers.GATE_TABLE`, consumers `client`). Adjust `validate_feature_wirings` to accept module-level data exports (not just callables) for the `GATE_TABLE` entry. | `validate_feature_wirings` must handle non-callable producers without raising on the callable check. |
| `src/pageindex_mcp/server.py` | 49-119 | Call `validate_feature_wirings()` in the lifespan hook so wiring is validated at process start | Import and call after registry init, before `yield`. Wrap to log and re-raise so failure is visible. | If `validate_feature_wirings` raises, server must refuse to start — do not swallow `AssertionError`. |
| `src/pageindex_mcp/worker.py` | 861-879 | Call `validate_feature_wirings()` in `startup()` coroutine | Import and call after registry init (function is sync — call directly, no `await`). | Worker must refuse to start on failure. |
| `src/pageindex_mcp/server.py` | after line 41 | Expose `storage.delete_doc` as an MCP tool/route so HR2 cascade is reachable in production (CLAUDE.md Hard Rule 2) | Add `async def delete_document(doc_id: str)` calling `storage.delete_doc(doc_id)`, returning its result dict. Gate behind existing `UPLOAD_API_KEY` auth. | Must be authenticated. Must return the `errors` list so partial failures are visible. Must not bypass any cascade step (uploads/ → processed/*.json → processed/*.meta.json → Redis cache → documented backups, in that order per Hard Rule 2). |

**Wiring checks (corrected — see validation major issues):**

| Symbol | Consumers required | Check type | Correction |
|---|---|---|---|
| `PipelineConfig` | `src/pageindex_mcp/helpers.py` **only** | import | Original listed `config.py` as a consumer of a symbol `config.py` itself defines — vacuous self-file check, dropped. |
| `pipeline_config` | `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/client.py` | import | **Gap:** no Zone 5 code_target currently touches `client.py`. Either add a `client.py` code_target replacing its scattered env reads with `pipeline_config`, or drop `client.py` from this check. Flagged, unresolved — do not mark this check as passable until one of those is done. |
| `validate_feature_wirings` | `src/pageindex_mcp/server.py`, `src/pageindex_mcp/worker.py` | call | — |
| `delete_document` registered as MCP tool/route in `server.py` | — | call (route-registration decorator), not "imported by" | Original self-file "imported by server.py" check was vacuous (defined there); replaced with a registration check. |
| `gate_recovery_dispatch` (literal string, not the full `FeatureWiring(...)` constructor expression) | appears in the `FEATURE_WIRINGS` list literal in `helpers.py` | contract/grep | Original symbol was a full constructor call, not greppable; corrected to the literal name. |
| `rtl_decision` (literal string) | appears in the `FEATURE_WIRINGS` list literal in `helpers.py` | contract/grep | Same correction as above. |
| `VerdictThresholds.from_config` | consumed by `classify_verdict` | call | **Gap:** no wiring_check exists for this renamed symbol in the original spec — added here. |
| `reset_pipeline_config` | consumed by test fixtures | call | **Gap:** no wiring_check exists for this new symbol in the original spec — added here. |

**Test requirements:**
- `tests/test_zone5_config_freeze.py` — `PipelineConfig.from_env()` reads each env var once; matches prior `effective_config_snapshot()` values; freeze guarantee (post-instantiation `os.environ` mutation has no effect); `reset_pipeline_config()` produces a fresh instance.
- `tests/test_zone5_config_freeze.py` — `effective_config_snapshot()` returns `dataclasses.asdict(pipeline_config)`; zero `os.environ` calls at call time (mocked).
- `tests/test_zone5_config_freeze.py` — exhaustiveness: every `PipelineConfig` field has a matching `effective_config_snapshot()` key; no env var read by both `PipelineConfig.from_env` and a module-level `helpers.py` constant (drift-reintroduction guard).
- `tests/test_zone5_config_freeze.py` — import-time assertion fires when `PASS_MAX_LEAF_RATIO > LEAF_SPLIT_RATIO` (corrected direction).
- `tests/test_zone5_wiring_startup.py` — no `atexit.register(validate_feature_wirings)` in source; `server.py` lifespan calls it explicitly.
- `tests/test_zone5_wiring_startup.py` — `FEATURE_WIRINGS` has ≥6 entries including `gate_recovery_dispatch` and `rtl_decision`.
- `tests/test_zone5_hr2_endpoint.py` — MCP tool/route delegates to `storage.delete_doc`; unauthenticated requests rejected (401/403).
- `tests/test_zone5_config_freeze.py` — regression: `_get_verdict_thresholds`/`_verdict_thresholds_cache` no longer exist in `helpers` module namespace.

**Corpus validation:** all documents (config freeze affects every ingestion path). Expected direction: **stable**. Spot-check count: 5.

**Estimated complexity:** medium. **Severity:** high.

---

### Zone: Zone 6 — Cross-Process Error Classification Boundary (wave 1, priority 3)

**⚠ Wiring checks require correction** — see [Validation Results](#validation-results) major issue.

**Mechanism to eliminate:** Three interacting defects in the worker-child error classification boundary: (1) `_CHILD_ERROR_REASON` maps only 4 exception class names while the child (`converters_cli.py:176`) can emit 7+ distinct `type(exc).__name__` values — unmapped names fall through to `'converter_child_failed'`, which is not in `_TERMINAL_CHILD_REASONS`, so deterministic failures retry up to `MAX_TRIES`, wasting worker slots. (2) `_TERMINAL_CHILD_REASONS` has only 2 entries vs. 10+ gate defect reasons `validate_tree` can produce — no coverage assertion exists. (3) `reap_stale_jobs` uses a fixed cutoff `JOB_TIMEOUT+REAP_GRACE=3750s` while `_run_converter_subprocess` grants up to `effective_timeout*16.5=59,400s` for scanned/image PDFs via `PDF_INSPECTOR_PRECLASSIFY`. A legitimately-processing OCR job gets reaped to ERROR; when the child finishes successfully, `_set_job_status(DONE)` raises `ValueError` (`ERROR->DONE` not in `_VALID_TRANSITIONS`), propagates to the catch-all handler which re-stamps ERROR, and the successfully-processed document is never recorded (doc_id lost, registry row never written).

**Strategy:** Three-part fix: (A) Replace the open-ended string-keyed `_CHILD_ERROR_REASON` dict with a `ChildErrorClassification` enum+registry, exhaustiveness-asserted against the set of exception classes the child can actually emit, each classified terminal (no retry) or transient (retryable). (B) Add a per-job `effective_timeout_at` field to the Redis job hash so `reap_stale_jobs` respects dynamic timeouts instead of a fixed cutoff. (C) Add `ERROR->DONE` to `_VALID_TRANSITIONS` for the narrow case where a reaped job's child actually succeeded, with a `late_success` flag so the outcome is observable.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/worker.py` | 111-129 | Replace `_CHILD_ERROR_REASON` with `ChildErrorClassification` frozen dataclass and `_CHILD_ERROR_REGISTRY: dict[str, ChildErrorClassification]` covering all 7+ known child exception names; add exhaustiveness assertion at module load | `ChildErrorClassification(reason: str, terminal: bool)`. Map `TessdataUnavailableError→converter_env_missing (terminal)`, `HeaderNotFoundException→converter_child_failed (transient)`, `ImplausibleHeadingStructureException→converter_child_failed (transient)`, `FuturesTimeoutError→converter_timeout (terminal)`, `TypeError→converter_child_failed (transient)`. Derive `_TERMINAL_CHILD_REASONS = frozenset(c.reason for c in registry.values() if c.terminal) \| {'llm_failure_terminal'}`. Replace the lookup at the classification call site with `_CHILD_ERROR_REGISTRY.get(exc.error_class or '', _DEFAULT_CHILD_CLASSIFICATION).reason`. | Must not change Redis reason strings for `LowQualityTreeError`/`LLMTransientFailure` (dashboards depend on them). Must preserve `'converter_child_failed'` default for unknown exception classes (forward compat). |
| `src/pageindex_mcp/worker.py` | 312-350, 444-453, 630-681 | Record per-job `effective_timeout_at` in Redis hash when transitioning to PROCESSING; read it in `reap_stale_jobs` instead of the fixed cutoff | Move the `PDF_INSPECTOR_PRECLASSIFY` 16.5x timeout-multiplier logic out of `_run_converter_subprocess` into `process_document_job`, writing `effective_timeout_at = int(time.time()) + effective_timeout + REAP_GRACE` to the Redis hash in the same `hset` call as the PROCESSING status write. `reap_stale_jobs` reads `effective_timeout_at` when present as the absolute deadline; falls back to `processing_started_at + JOB_TIMEOUT + REAP_GRACE` for legacy jobs missing the field. | Must preserve backward compatibility for jobs without `effective_timeout_at`. Must not create a race where `effective_timeout_at` is never written — write both fields in the same `hset` call. |
| `src/pageindex_mcp/job_status.py` | 36-44 | Add `ERROR->DONE` to `_VALID_TRANSITIONS` so a reaped-then-completed job can record its success | Change `JobStatus.ERROR: frozenset({JobStatus.ERROR})` to `frozenset({JobStatus.ERROR, JobStatus.DONE})`. Add comment explaining the narrow reap-recovery case. Transition writes a `late_success=true` field. | Must NOT allow `DONE->ERROR` or any other new transition. `ERROR->DONE` path used only in `process_document_job`'s success path, distinguishable via `late_success`. |
| `src/pageindex_mcp/worker.py` | 548-576 | Wrap `_set_job_status(DONE)` in the success path with a `try/except ValueError` safety net that still writes doc_id and registry row | Log WARNING with doc_id on the (now theoretically unreachable post-fix) `ValueError` path; still call `_upsert_registry_row` and return doc_id. On success from ERROR state, set `late_success='true'` and `reaped_recovery='true'`. | Must not swallow the exception silently. Must still call `_upsert_registry_row` even on late success. |
| `src/pageindex_mcp/worker.py` | 124-129 | Module-level exhaustiveness assertion: every terminal reason in `_CHILD_ERROR_REGISTRY` is in `_TERMINAL_CHILD_REASONS` and vice versa | `assert _TERMINAL_CHILD_REASONS == frozenset(...) | {'llm_failure_terminal'}` with a descriptive symmetric-diff error message, fired at import time. | Assertion must not fire for `'llm_failure_terminal'` (produced by `_classify_llm_failure`, not registry lookup). |

**Wiring checks (corrected — see validation major issue):**

| Symbol | Check | Correction |
|---|---|---|
| `_CHILD_ERROR_REGISTRY` referenced at the classification call site (`worker.py` ~line 503) | call-site usage, not self-file import | Original listed self-file "imported by worker.py" for symbols defined in worker.py — vacuous. |
| `effective_timeout_at` written in the Redis `hset` call **and** read in `reap_stale_jobs` (two distinct call sites) | two separate call checks | Original collapsed to one vacuous "worker.py" entry; must verify both the write site and the read site independently. |
| `JobStatus.ERROR` transition set contains `JobStatus.DONE` in `job_status.py` | contract check on `_VALID_TRANSITIONS` | **Gap:** the original wiring_checks had zero entries for `job_status.py` despite it carrying the single most safety-critical change in this zone. Added here. |
| `late_success` flag referenced in `worker.py`'s success-path handling | call | **Gap:** added to verify the success path actually consumes the new transition, not just that `job_status.py` permits it. |

**Test requirements:**
- `tests/test_zone6_error_classification.py` — exhaustiveness: every exception class name `converters_cli.py` can emit is a key in `_CHILD_ERROR_REGISTRY` (AST-parsed from raise statements that propagate to the child's catch-all).
- `tests/test_zone6_error_classification.py` — `_TERMINAL_CHILD_REASONS` equals the derived set; no reason is terminal in one place and transient in the other.
- `tests/test_zone6_error_classification.py` — regression: `LowQualityTreeError→low_quality_tree` (terminal); `RuntimeError→converter_child_failed` (not terminal); empty/None `error_class` → default (not terminal).
- `tests/test_zone6_reap_timeout.py` — contract: job with `effective_timeout_at` in the future is not reaped; in the past is reaped; missing field falls back to legacy fixed cutoff.
- `tests/test_zone6_reap_timeout.py` — integration: 16.5x `PDF_INSPECTOR_PRECLASSIFY` multiplier reflected in `effective_timeout_at`; not reaped within that window.
- `tests/test_zone6_job_status.py` — contract: `ERROR->DONE` allowed; `DONE->ERROR` still forbidden; write succeeds without `ValueError`.
- `tests/test_zone6_late_success.py` — integration: full scenario — job reaped mid-processing, child completes, `process_document_job` writes DONE with `late_success`, doc_id returned, `_upsert_registry_row` called. No data loss.

**Corpus validation:** warid-597, world-stats-pocketbook-2023. Expected direction: **stable**. Spot-check count: 3.

**Estimated complexity:** medium. **Severity:** critical.

---

### Zone: Zone 7 — Mutable ExtractionState Recovery Path Ordering (wave 1, priority 4)

**Note on depends_on naming collision (validation minor issue):** the spec's `depends_on` reads `"zone-3 (RecoveryOutcome dataclass must exist -- already landed cfbf1a1)"` — this is an already-landed commit reference, **not** a dependency on this batch's "Zone 3: Garble Detection Heuristic Patchwork" (wave 2). Any scheduler resolving by zone name must not treat Zone 7 (wave 1) as depending on the later Zone 3. Track this dependency as `landed:cfbf1a1` instead.

**Mechanism to eliminate:** Mutable in-place state mutations across 4 sequential recovery attempts (GARBLE, LOW_CONTENT, IMAGE_DOMINANT OCR retry, VLM fallback) with incomplete snapshot/revert: `RecoveryOutcome` captures 8 of 12 recovery-relevant `ExtractionState` fields, missing `tmp_md_path` (pre-retry tempfile unlinked by `_reconvert_and_revalidate`, pointer never reverted), `route`, `rtl_decision`, and `bidi_renorm_applied`. When keep-best determines retry lost, `apply()` reverts `md_content` but leaves `state.tmp_md_path` pointing at a post-retry tempfile. Separately, `_renormalize_bidi_guarded` (`reconstruct_bidi_order`) runs on remote markdown in `_convert_to_tree` and again in `_recover_ocr_retry`, with no idempotency guard preventing `_recover_rtl_repair` from per-node double-applying on already-corrected headings, collapsing bilingual content structure (MOU: 134 nodes → 20 nodes). Arabic structural heading injection via `reconstruct_bidi_order` changes `validate_tree` node_count/depth signals, forcing shallow docs past validation even when the tree path is 80% content-lossy vs. flat routing (marsoom-13: 6 nodes/1225 chars tree vs 75 blocks/5972 chars flat).

**Strategy:** Three-pronged fix: (1) Expand `RecoveryOutcome` snapshot to cover all recovery-relevant `ExtractionState` fields (`tmp_md_path`, `route`, `rtl_decision`, `bidi_renorm_applied`) so keep-best revert restores fully consistent state; (2) Add `bidi_renorm_applied` tracking flag to `ExtractionState` with a guard in `_recover_rtl_repair` preventing double-application of `reconstruct_bidi_order`; (3) Add a post-recovery content-quality assertion comparing tree total_chars vs. flat block chars when the tree path is structurally inflated by heading injection — logging but not auto-routing, preserving existing route dispatch authority.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/helpers.py` | 205-237 | Add `bidi_renorm_applied: bool = False` field to `ExtractionState` | After `flat_garble_unrecovered` (line 232). | `ExtractionState` used at 6+ constructor call sites — new field must default so all remain valid. |
| `src/pageindex_mcp/helpers.py` | 153-196 | Add `tmp_md_path` and `bidi_renorm_applied` fields to `RecoveryOutcome`; extend `apply()` | Add `tmp_md_path: str \| None \| _Unset = _UNSET` and `bidi_renorm_applied: bool \| _Unset = _UNSET` with corresponding `isinstance` checks in `apply()`. | `RecoveryOutcome` is `frozen=True`. New fields default to `_UNSET`; `apply()` must not write `_UNSET` fields. Existing `test_zone3_recovery_pipeline` tests must still pass. |
| `src/pageindex_mcp/client.py` | 1388-1397 | Expand pre-retry `RecoveryOutcome` snapshot to include `tmp_md_path`, `route`, `rtl_decision`, `bidi_renorm_applied` | Add the four kwargs to the constructor call, capturing values before OCR retry mutates state. | Snapshot must capture values BEFORE mutation; no reordering relative to OCR dispatch. |
| `src/pageindex_mcp/client.py` | 1196-1199 | Set `bidi_renorm_applied=True` after `_renormalize_bidi_guarded` in `_convert_to_tree` | Add the assignment inside the `if state.use_remote and REMOTE_MD_RENORMALIZE` block. | Must be inside that guard, not unconditional. Local conversions handle bidi separately in `converters.py` and must not set this flag from `client.py`. |
| `src/pageindex_mcp/client.py` | 1442-1476 | Reset `bidi_renorm_applied` before OCR dispatch, set after `_renormalize_bidi_guarded` in `_recover_ocr_retry` | Reset to `False` before dispatch (new content about to be extracted); set `True` after `_renormalize_bidi_guarded`; set `False` in the local-OCR-retry else branch. | Reset must come AFTER the pre-retry snapshot so the snapshot captures the correct pre-retry value. |
| `src/pageindex_mcp/client.py` | 1572-1613 | Add bidi double-application guard to `_recover_rtl_repair` | After the existing early-return guard, add: if `state.bidi_renorm_applied`, log and return — skip per-node `reconstruct_bidi_order`. | Guard must come AFTER the `first_defect==RTL_REVERSAL` check. Must NOT suppress RTL repair when bidi normalization was not applied (local conversion path). |
| `src/pageindex_mcp/client.py` | 1548-1556 | Clarify (comment only) keep-best revert tempfile handling now that `tmp_md_path` is restored by `apply()` | Add comment documenting that the pre-retry tempfile was unlinked by `_reconvert_and_revalidate`, so re-materialization at 1552-1556 is intentional. No behavioral change. | Tempfile re-creation logic must not be removed. |

**Wiring checks:**

| Symbol | Consumers | Check type |
|---|---|---|
| `ExtractionState.bidi_renorm_applied` | `src/pageindex_mcp/client.py` | dispatch |
| `RecoveryOutcome.tmp_md_path` | `src/pageindex_mcp/client.py` | call |
| `RecoveryOutcome.bidi_renorm_applied` | `src/pageindex_mcp/client.py` | call |

**Test requirements:**
- `tests/test_zone7_recovery_revert.py` — `RecoveryOutcome.apply()` restores all recovery-relevant fields (`tmp_md_path`, `route`, `rtl_decision`, `bidi_renorm_applied`); `_UNSET` fields leave state unchanged; revert restores `route` and `bidi_renorm_applied` correctly.
- `tests/test_zone7_bidi_double_apply_guard.py` — contract: `bidi_renorm_applied=True` short-circuits `_recover_rtl_repair`; `False` proceeds normally; OCR retry resets/sets the flag correctly; keep-best revert restores pre-retry value; `_convert_to_tree` sets it only for remote+`REMOTE_MD_RENORMALIZE`.
- `tests/test_zone7_recovery_revert.py` — regression: RFC-029 D4 cabinet resolution keep-best revert (48k→14.8k chars) fully restores pre-retry state including `tmp_md_path` pointing at a valid on-disk tempfile.
- `tests/test_zone7_bidi_double_apply_guard.py` — regression: RFC-034 D3/D17 MOU scenario — mixed-script doc where bidi already applied skips per-node repair, preserving node/char count.
- `tests/test_zone3_recovery_pipeline.py` — regression: existing `RecoveryOutcome` tests remain green with the expanded field set.

**Corpus validation:** MOU, cabinet_resolution, marsoom-13, GHV-TKV-Tarif. Expected direction: **improve**. Spot-check count: 4.

**Estimated complexity:** medium. **Severity:** high.

---

### Zone: Zone 1 — GATE_TABLE to Recovery Dispatch Reason-Code Coupling (wave 2, priority 5)

**⚠ Spec requires correction** — see [Validation Results](#validation-results) blocker #4 (sequencing), major issue (flag placement), major issue (wiring gap on string-to-method binding).

**Mechanism to eliminate:** Two-site maintenance contract between `GateSpec` declarations in `helpers.py` (`GATES` list) and recovery eligibility logic in `client.py` (`_recover_ocr_retry`, `_recover_vlm_fallback`). Adding a new `GateSpec` with `recovery_tag='ocr_escalation'` makes the dispatch loop fire, but per-`OcrRetryReason` eligibility guards inside `_recover_ocr_retry` silently reject the new defect if its `TreeDefect` value is not in a hardcoded set. `_recover_vlm_fallback` similarly hardcodes `first_defect in (GARBLING, NODE_GARBLING)`, ignoring `NODE_COUNT_LOW`/`DEPTH_LOW` docs that also carry `ocr_escalation`. The `recovery_tag` wiring assertion confirms the tag key exists in `_recovery_dispatch` but cannot verify the per-reason eligibility predicates inside recovery methods accept every `TreeDefect` whose `GateSpec` carries that tag. Historical evidence: 12 bugs across RFC-018/025/029/030/036/016/023/027/028 all stem from this same gap.

**Strategy:** Move per-defect recovery eligibility from `client.py`'s recovery method bodies into the `GateSpec` declaration in `helpers.py` as a declarative `recovery_eligible` predicate and `recovery_fns` tuple, making `GateSpec` the single source of truth for both gate evaluation AND recovery dispatch. The recovery loop in `client.py` becomes a pure `GateSpec` iterator: `gate.recovery_eligible(state)` then each fn in `gate.recovery_fns`, eliminating `_recovery_dispatch`, `OcrRetryReason`, and `_seen_tags` dedup. Split `_recover_ocr_retry` into three focused methods (`_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_image_dominant_ocr`) sharing an `_execute_ocr_retry` helper. Add an import-time exhaustiveness assertion: every `GateSpec` with policy `RETRY_OCR`/`RETRY_RTL` must have non-empty `recovery_fns` and a non-`None` `recovery_eligible`.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/helpers.py` | 262-292, 1952-1969 | Extend `GateSpec` with `recovery_eligible` and `recovery_fns`; define module-level eligibility predicates | `recovery_eligible: Callable[[ExtractionState], bool] \| None = None`, `recovery_fns: tuple[str, ...] = ()`. Remove `recovery_tag` (replaced). Define `_eligible_garble`, `_eligible_low_content`, `_eligible_image_dominant`, `_eligible_rtl` as standalone module-level predicates. Update each `GateSpec` in `GATES` with the matching predicate and `recovery_fns` tuple. **Correction (validation major issue):** the spec's "how" text says flag gates (`_OCR_ESCALATION_GARBLE`, `_IMAGE_DOMINANT_OCR_ESCALATION_ENABLED`) "stay in client.py module scope" while predicates live in `helpers.py` — this is a circular import (`helpers.py` cannot import `client.py` module flags; `client.py` imports `helpers.py`). **Corrected approach:** move these flag reads into `PipelineConfig` (Zone 5's construct, wave 1) so `helpers.py` predicates read `pipeline_config.ocr_escalation_garble_enabled` etc. directly, with no cross-import. | `GATE_TABLE`, `REASON_POLICY`, `HARD_FAIL_DEFECTS`, `_GATE_PRIORITY` derivations must continue working unchanged. |
| `src/pageindex_mcp/helpers.py` | 1986-1994 | Bidirectional import-time exhaustiveness assertion: `RETRY_OCR`/`RETRY_RTL` gates have non-empty `recovery_fns`+`recovery_eligible`; non-empty `recovery_fns` implies `RETRY_OCR`/`RETRY_RTL` policy | Replace prior `recovery_tag`-based assertion. | Must crash at import time on violation. No runtime behavior change for `RAISE`/`OK`/`CAP_MARGINAL`/`PERSIST_FAIL` gates. |
| `src/pageindex_mcp/helpers.py` | 240-249 | Delete `OcrRetryReason` enum | Remove `GARBLE`/`LOW_CONTENT`/`IMAGE_DOMINANT` values, replaced by per-gate predicates. All importers updated in the same change. | Must coordinate with `client.py` changes simultaneously; test files importing `OcrRetryReason` must update too. |
| `src/pageindex_mcp/client.py` | 1328-1570 | Split `_recover_ocr_retry` into `_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_image_dominant_ocr`, sharing `_execute_ocr_retry` | Each method contains its branch's pre-retry snapshot / keep-best / dispatch logic with NO `first_defect` guard (eligibility already checked by `GateSpec.recovery_eligible`). Shared OCR dispatch/splice/revalidation extracted into `_execute_ocr_retry(state, file_path, filename, ext, expected_script, use_keep_best, label, splice_label)`. Net ~-80 lines. | Keep-best heuristic identical for GARBLE/LOW_CONTENT. IMAGE_DOMINANT still accepts unconditionally. `OCR_ESCALATION_TOTAL` metric labeling preserved. |
| `src/pageindex_mcp/client.py` | 1666-1672 | Remove hardcoded defect-type guard from `_recover_vlm_fallback` | Remove `first_defect in (GARBLING, NODE_GARBLING)` check; eligibility now declared via which `GateSpec`s list `_recover_vlm_fallback` in `recovery_fns`. Retain `state.ok==False`, `.pdf` ext, `settings.vlm_fallback` checks. | VLM fallback must still only fire for garble-type defects — enforced by `GateSpec` membership, not the method's own guard. |
| `src/pageindex_mcp/client.py` | 2200-2261 | Replace `_recovery_dispatch` dict with `GateSpec`-driven loop | Iterate `GATES`; for each gate with non-empty `recovery_fns`, call `gate.recovery_eligible(state)`; if `True`, resolve each name via `getattr(self, fn_name)` and await. Delete `_recovery_dispatch`, `_seen_tags`, the old tag-subset assertion. Re-derive `state.first_defect`/`state.route` after each gate's recovery block. | Must iterate in `GATES` severity order. `_recover_flat_prefer`/`_recover_landscape_reroute` remain outside the loop. |
| `src/pageindex_mcp/client.py` | 59 | Remove `OcrRetryReason` import | — | Must compile cleanly; no other file imports it after deletion. |

**Wiring checks (corrected — see validation major issue on string-to-method binding):**

| Symbol | Consumers | Check type | Correction |
|---|---|---|---|
| `GateSpec.recovery_eligible` referenced by evaluate_gates/recovery loop | `client.py` | dispatch | — |
| **`GateSpec.recovery_fns` string entries → real `CustomPageIndexClient` methods** | `src/pageindex_mcp/client.py` | dispatch — every string literal inside every `GateSpec.recovery_fns` tuple must resolve via `hasattr(CustomPageIndexClient, name)` and `inspect.iscoroutinefunction(getattr(...))` | **Gap added by validation:** this is the single most important wiring check for this zone — `recovery_fns` is a tuple of string names resolved via `getattr` at runtime; a typo reproduces exactly the class of bug this zone claims to fix (RFC-018/025/029/030/036/016/023/027/028), and none of the original wiring_checks caught it (only covered indirectly by a test, not by the authoritative wiring-check gate). Must be added as its own entry, not left to tests alone. |
| `_eligible_garble`/`_eligible_low_content`/`_eligible_image_dominant`/`_eligible_rtl` | consumed by `GATES` entries in `helpers.py` | call | Original self-file "imported by helpers.py" entries were vacuous; corrected to "referenced in GATES list" usage checks. |
| `_recover_garble_ocr`/`_recover_low_content_ocr`/`_recover_image_dominant_ocr`/`_execute_ocr_retry` | referenced in `GateSpec.recovery_fns` strings AND callable on `CustomPageIndexClient` | dispatch | Same correction as above — merge into the string-to-method binding check rather than standalone self-file entries. |

**Test requirements:**
- `tests/test_zone1_recovery_contract.py` — import-time exhaustiveness: `RETRY_OCR`/`RETRY_RTL` gates have `recovery_fns`+`recovery_eligible`; reverse direction too; mock `GATES` list to prove violation raises.
- `tests/test_zone1_recovery_contract.py` — eligibility predicates accept exactly the correct `TreeDefect` values, parametrized over all `TreeDefect` members.
- `tests/test_zone1_recovery_contract.py` — `recovery_fns` strings resolve to real, async methods on `CustomPageIndexClient` (this is the test-level counterpart to the new wiring_check above — both must exist, not just the test).
- `tests/test_zone1_recovery_contract.py` — regression: RFC-029 D1-D2 (PERSIST_FAIL gates fire no recovery), RFC-018 D3b (NODE_GARBLING fires `ocr_escalation`), RFC-036 D3 (RTL_REVERSAL fires `rtl_repair`, not terminal raise).
- `tests/test_zone1_recovery_contract.py` — VLM fallback fires only for `GARBLING`/`NODE_GARBLING` `GateSpec`s.
- `tests/test_zone1_recovery_contract.py` — recovery loop iterates in `GATES` severity order (mock side_effects to track call order).
- `tests/test_zone1_gate_table.py` — `OcrRetryReason` import fails; `recovery_tag` field no longer exists on `GateSpec`; `recovery_eligible`/`recovery_fns` fields exist.

**Corpus validation:** warid-597, Penal_Code, federal_decree_law_no_33, marsoom-33, al-qarar, siyasat-hawkama, Human-Rights. Expected direction: **stable**. Spot-check count: 7.

**Estimated complexity:** large. **Severity:** critical.

---

### Zone: Zone 3 — Garble Detection Heuristic Patchwork (wave 2, priority 6)

**⚠ Spec requires correction** — see [Validation Results](#validation-results) major issues (env-var double ownership, stale `reset_verdict_thresholds` reference, `_script_from_filename` placement contradiction, wiring gap).

**Mechanism to eliminate:** Each garble heuristic prong self-infers script context independently (4 scattered inference sites), reads its own env vars inline (7 scattered `os.environ` reads), and is calibrated to one document. Produces structural recurrence: (a) NFKC normalization decomposes Presentation Forms (U+FB50-FEFF) to base Arabic (U+0600-06FF) *before* the reversed-morphology detector runs, yielding 0% TPR (RFC-033 D2); (b) `_script_from_filename` returns `None` for German filenames, so `expected_script != 'Latn'` never fires (RFC-019 D2, RFC-013/015); (c) the garble gate inspects `node.text` but never `node.title`, leaving 23/24 reversed RTL titles in siyasat-hawkama invisible (RFC-030 D4); (d) `_check_bidi_coherence` was dead code, symptomatic of fragmented entry points.

**Strategy:** Consolidate all garble detection into a single-entry-point pipeline: (1) `ScriptContext` frozen dataclass computed once per document at index entry, carrying `dominant_script`, `had_presentation_forms` (captured pre-NFKC), and source provenance — eliminating the 4 scattered inference sites; (2) `GarbleConfig` frozen dataclass consolidating the 7 scattered env reads; (3) `detect_garble(text, title, ctx, config) -> GarbleReport` unified function replacing `check_garble` + `garble_prongs` + `_garble_ratio` — no prong self-infers script, no prong reads env vars, `node.title` inspected alongside `node.text`. Migration sequenced as additive-then-subtractive steps, each independently deployable with a corpus regression gate.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/script.py` | after `RtlDecision` (~458+) | Add `ScriptContext` frozen dataclass with factory `from_document(filename, raw_text)` | Fields: `dominant_script`, `had_presentation_forms`, `source`. Factory scans `raw_text` for Presentation Forms ratio BEFORE any NFKC normalization runs, falls back to filename inference, then `infer_script(raw_text)`. **Correction (blocker/major issue on `_script_from_filename` placement):** the original spec claims `_script_from_filename` lives in `converters.py` and must be late-imported from there, while also requiring `script.py` not import from `helpers.py` — but `_script_from_filename` is actually defined in `helpers.py:1654`, and no code_target moves it. **Corrected code_target:** move `_script_from_filename` (currently `helpers.py:1654`) into `script.py` itself (or reimplement its logic there directly), so `ScriptContext.from_document` can call it without any cross-module import. | `ARABIC_RANGES`, `PRESENTATION_RANGES`, `infer_script` already live in `script.py` — reuse them. `script.py` remains a dependency-free leaf module (Zone 5 constraint). |
| `src/pageindex_mcp/helpers.py` | 1425-1428, 1471-1472, 1584-1586 | Consolidate 7 garble env var reads into `GarbleConfig` frozen dataclass | **Correction (blocker on env-var double ownership with Zone 5):** the original spec has `GarbleConfig.from_env()` re-read `GARBLE_NODE_RATIO_THRESHOLD`, `GARBLE_SHORT_TEXT_DEFAULT`, `GARBLE_FLAT_MARKDOWN_NORMALIZE` directly from `os.environ` — but Zone 5 (wave 1, lands first) absorbs exactly these vars into `PipelineConfig` and adds an exhaustiveness test asserting no env var is read by both `PipelineConfig.from_env` AND a module-level `helpers.py` constant. As written, Zone 3 directly violates that guard and reintroduces the freeze drift Zone 5 eliminates. **Corrected approach:** `GarbleConfig` becomes a typed subset view built via `GarbleConfig.from_config(pipeline_config)`, not a second `from_env` reader — it reads its 7 fields off the already-instantiated `pipeline_config` singleton, not off `os.environ` directly. | All 7 default values must match current scattered defaults exactly (see test requirements). |
| `src/pageindex_mcp/helpers.py` | (post-Zone-5) | Reset hook for tests | **Correction (major issue — stale reference):** original said "update `reset_verdict_thresholds()` to also reset `_garble_config`" — but Zone 5 deletes `reset_verdict_thresholds` and replaces it with `reset_pipeline_config()`. **Corrected:** `reset_pipeline_config()` (Zone 5's replacement) must also reset any cached `GarbleConfig` view. | — |
| `src/pageindex_mcp/helpers.py` | 1342-1548 | Unify `garble_prongs` + `check_garble` into `detect_garble(text, ctx, config) -> GarbleReport`; keep `check_garble` as thin backward-compat wrapper for one release cycle | `GarbleReport` frozen dataclass (`fired_prongs`, `is_garbled`, `garble_ratio`). `detect_garble(text, *, title='', script_context, config, blob_kind=BlobKind.TREE_TEXT, original_defect=None)`. All prong checks use `script_context.dominant_script`/`had_presentation_forms` — never self-infer. Title garble check added. `check_garble` rewritten as thin wrapper delegating to `detect_garble`. Delete `GarbleProfile`, `BULK_PROFILE`, `FLAT_MARKDOWN_PROFILE` (replaced by `BlobKind`+`GarbleConfig`). | `check_garble` must remain callable with its current signature for one release cycle. Property-based equivalence test required across corpus samples. |
| `src/pageindex_mcp/helpers.py` | 1670-1718, 1768-1784 | Thread `ScriptContext` through `_garble_check_nodes`/`_gate_node_garbling`, eliminate per-node `_infer_script` re-inference | Signature changes to accept `script_context`+`config`. QF3 bilingual per-node override logic preserved but sourced from `script_context`, not a direct `_infer_script` call. | QF3 bilingual node behavior (RFC-021) must be preserved exactly. |
| `src/pageindex_mcp/helpers.py` | 441-499 | `TreeSignals.from_tree` accepts `ScriptContext` instead of `expected_script: str` | `script_context.dominant_script` replaces the `expected_script or _infer_script(...)` fallback chain. All callers updated. | `TreeSignals` field types unchanged — `GarbleReport` values map 1:1. |
| `src/pageindex_mcp/client.py` | 2153, 466, 1057-1061, 1507-1524, 1818-1848 | Compute `ScriptContext` once at index entry, thread through all garble call sites | Replace `_script_from_filename(filename)` call with `ScriptContext.from_document(filename, raw_first_page_text)`, extracted pre-NFKC. All downstream `check_garble` calls replaced with `detect_garble` passing the single `script_context`. | `raw_first_page_text` must be extracted before any NFKC normalization. Non-PDF files: `from_document(filename, '')` acceptable. |
| `src/pageindex_mcp/converters.py` | 1783-1785, 1895-1897 | Replace `check_garble` calls with `detect_garble` using threaded `ScriptContext` | `_text_layer_has_content` and `_document_level_text_fallback` accept `script_context` parameter instead of `expected_script: str`. **Gap flagged by validation (major issue):** the spec never states where `converters.py`'s late-import block obtains a `GarbleConfig` instance for the `config=` kwarg `detect_garble` requires — must also late-import `_garble_config` (or a `GarbleConfig.from_config(pipeline_config)` accessor) alongside `detect_garble` in this same block. | Late-import pattern preserved to avoid circular import between `converters.py` and `helpers.py`. `ScriptContext` threaded from `client.py`, not re-computed. |
| `src/pageindex_mcp/helpers.py` | 1569-1582, 2051 | Delete dead-code comment artifacts (`_has_sparse_mojibake` remnant, `_check_bidi_coherence` deletion comments) | Pure comment deletion. | Zero behavioral change. |

**Wiring checks (corrected — see validation major issue):**

| Symbol | Consumers | Check type | Correction |
|---|---|---|---|
| `ScriptContext` | `helpers.py`, `client.py` | import | — |
| `ScriptContext.from_document` | `client.py` | call | — |
| `GarbleConfig.from_config` (renamed from `from_env` per correction above) | `helpers.py` | call | Corrected from `from_env` — this zone no longer reads env directly. |
| `detect_garble` | `helpers.py`, `client.py`, `converters.py` | call | — |
| `_garble_config` accessor (or equivalent) available to `converters.py`'s late-import block | `converters.py` | import | **Gap added by validation:** original only checked `detect_garble` reaches `converters.py`, not that a valid `GarbleConfig` instance is reachable there too. |
| `GarbleReport` | `helpers.py`, `client.py` | import | — |

**Test requirements:**
- `tests/test_zone3_script_context.py` — `ScriptContext.from_document`: Arabic/German filenames, hash-named fallback, Presentation Forms ratio detection surviving post-NFKC, source provenance.
- `tests/test_zone3_garble_config.py` — `GarbleConfig.from_config(pipeline_config)` (corrected from `from_env`): defaults match current scattered defaults exactly (`GARBLE_LATIN_GIBBERISH_ENABLED=true`, `GARBLE_LATIN_RATIO=0.4`, `GARBLE_NONSENSE_RATIO=0.7`, `GARBLE_SHORT_TEXT_DEFAULT=true`, `GARBLE_FLAT_MARKDOWN_NORMALIZE=true`, `GARBLE_NODE_RATIO_THRESHOLD=0.10`, `GARBLE_DIGIT_FLOOR=500`); frozen/immutable.
- `tests/test_zone3_detect_garble.py` — regression: warid-597 Latin gibberish (RFC-019 D2), presentation_forms prong fires post-NFKC (RFC-028 D2/RFC-033 D2), title reversed-morphology detection (RFC-030 D4), German filenames don't false-fire (RFC-013/015), `BlobKind.RAW_MARKDOWN` normalization, sparse_mojibake prong uses un-normalized text.
- `tests/test_zone3_detect_garble_equivalence.py` — property-based equivalence between `detect_garble` and legacy `check_garble` across 100+ corpus blobs plus hypothesis-generated random inputs.
- `tests/test_zone3_no_self_inference.py` — AST-verified: zero `_infer_script`/`infer_script` calls inside `garble_prongs`/`detect_garble`; zero `os.environ.get`/`os.getenv` calls inside those function bodies (all env access routed through `pipeline_config`/`GarbleConfig`).
- `tests/test_zone3_client_script_context_threading.py` — integration: `client.py` computes `ScriptContext` exactly once per index() call; `converters.py` functions receive `ScriptContext` parameters, not raw strings.

**Corpus validation:** warid-597, siyasat-hawkama, huquq-al-insan, Haftpflicht-Besondere, marsoom-33, federal_decree_law_no_33, Penal Code. Expected direction: **improve**. Spot-check count: 7.

**Estimated complexity:** large. **Severity:** critical.

---

### Zone: Zone 4 — Verdict Threshold Oscillation and Hysteresis Failure (wave 3, priority 7)

**⚠ Spec requires correction** — see [Validation Results](#validation-results) blocker #1 (Zone 5 deletes symbols Zone 4 targets).

**Mechanism to eliminate:** Monolithic `compute_verdict` (226 lines, cyclomatic complexity 28) interleaves gate evaluation (Phase 1) and promotion/cap logic (Phase 2), making threshold changes to one phase silently affect the other. Each threshold widening (RFC-023 0.17→0.20, RFC-024 0.20→0.30) is calibrated to fix one document but admits a different previously-correctly-rejected one, creating an oscillation cycle. `find_prior_verdict` (the hysteresis mechanism meant to break this cycle) has ZERO production callers — its only callers are test files. `snapshot_prior_verdicts` runs inside `wipe_processed` but the snapshot is never consumed. `PASS_MAX_LEAF_RATIO` and `LEAF_SPLIT_RATIO` both default to 0.30 but are independently configurable with no coupling assertion.

**Strategy:** Decompose `compute_verdict` into two pure functions (`evaluate_gates` for Phase 1, `apply_promotions` for Phase 2) connected by a typed `GateOutcome` intermediate, reducing the monolith to a ~15-line dispatcher. Replace the dead hysteresis mechanism with a deterministic per-document verdict ledger persisted at `verdicts/{sha256}.json` — a MinIO prefix excluded from `wipe_processed` — so byte-identical content always anchors to its best prior verdict. Add the `PASS_MAX_LEAF_RATIO <= LEAF_SPLIT_RATIO` coupling assertion (single source of truth — do not duplicate Zone 5's assertion; see correction below).

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `src/pageindex_mcp/helpers.py` | 2486-2597 | Extract Phase 1 gate evaluation into `evaluate_gates()` returning typed `GateOutcome` | `GateOutcome(defect, validate_reason, signals, all_defects, hard_fail_verdict)`. **Correction (blocker #1):** the spec's "how" text has this function receive `VerdictThresholds` as a parameter directly — this remains valid post-Zone-5 provided `VerdictThresholds` is obtained via `VerdictThresholds.from_config(pipeline_config)` (Zone 5's renamed constructor), not the deleted `_get_verdict_thresholds()`. | All 21 existing `compute_verdict` callers, and 145 `classify_verdict` callers, must produce identical `(verdict, reason)` output. `FLAT_GATE_SUBSET` evaluation ordering preserved. |
| `src/pageindex_mcp/helpers.py` | 2600-2711 | Extract Phase 2 promotion/cap logic into `apply_promotions()` | `apply_promotions(gate_outcome, content_class, image_enrichment_ratio, inspector_class, th, source_selection) -> VerdictResult`. `_apply_clamp` closure becomes a local helper. Each promotion rule a named early-return, not nested if/elif. | Image-enrichment rescue MUST remain positioned before `max_leaf_ratio` hard-fail (RFC-022 B2 lock). `source_selection=True` skips `_clamp_pass`. Thresholds sourced from the `VerdictThresholds` (Zone-5-derived) instance, not raw `os.environ`. |
| `src/pageindex_mcp/helpers.py` | 2486-2711 | Rewrite `compute_verdict` as ~15-line thin dispatcher | **Correction (blocker #1):** original body reads `th = _get_verdict_thresholds()` — that function is deleted by Zone 5. **Corrected body:** `th = VerdictThresholds.from_config(pipeline_config)` (or read a module-level cached instance the same way Zone 5's `classify_verdict` does); `outcome = evaluate_gates(...)`; return hard-fail or `apply_promotions(...)`. | Function signature/return type unchanged. `classify_verdict` wrapper unchanged. |
| `src/pageindex_mcp/helpers.py` | 399-413 | `PASS_MAX_LEAF_RATIO <= LEAF_SPLIT_RATIO` coupling assertion | **Correction (blocker #2, cross-referenced):** this assertion is now owned by Zone 5's `PipelineConfig.from_env()` (with the corrected `<=` direction). Zone 4 must **reference** that single assertion, not re-add a second, differently-worded one in `VerdictThresholds.from_env()` — `VerdictThresholds.from_config(pipeline_config)` inherits the already-validated values, no independent assertion needed here. | Do not duplicate the assertion in two places with any risk of direction drift (this is exactly what caused blocker #2). |
| `src/pageindex_mcp/storage.py` | 862-991 | Replace dead hysteresis mechanism with deterministic verdict ledger at `verdicts/{sha256}.json` | Delete `find_prior_verdict`, `snapshot_prior_verdicts`, `_VERDICT_PRIORITY`, `_PRIOR_VERDICTS_KEY`. Add `persist_verdict_ledger(sha256, verdict, reason)` and `read_verdict_ledger(sha256) -> str \| None`, both against MinIO `verdicts/` prefix. Max-priority-wins guard (PASS > MARGINAL > FAIL > ERROR). | `verdicts/` prefix MUST NOT be wiped by `wipe_processed()`. |
| `src/pageindex_mcp/storage.py` | 994-1024 | Update `wipe_processed` to drop `snapshot_prior_verdicts` dependency | Remove the snapshot call and stat-object check; function becomes list-and-remove `processed/*` only. `verdicts/` is inherently safe (different prefix). | Must still work for corpus reingestion; must not touch `verdicts/` objects; must log removed-object count. |
| `src/pageindex_mcp/storage.py` | 545-660 | Wire `persist_verdict_ledger` into `save_doc_meta` | After the sidecar `put_object`, if `meta` has `verdict`+`sha256`, call `persist_verdict_ledger` (fire-and-forget, log warning on failure, don't raise). Respect `_verdict_cas_guard`/`_skip_verdict`. | Must not change `save_doc_meta`'s return type or error behavior. No added critical-path latency. |
| `src/pageindex_mcp/client.py` | 1784-1978 | Wire `read_verdict_ledger` into `_persist_flat_result` for hysteresis anchoring | After `compute_verdict` returns, check `read_verdict_ledger(sha256)`; if prior verdict has higher priority, override with `anchored_by_ledger` reason annotation. | Non-blocking, graceful degradation on MinIO unavailability. No signature change. |
| `src/pageindex_mcp/client.py` | 1980-2113 | Wire `read_verdict_ledger` into `_persist_tree_result` for hysteresis anchoring | Same logic as flat path. **Distinct wiring check required for this call site — see below.** | Same constraints as flat-result wiring. |

**Wiring checks (corrected — see validation minor issue splitting the two call sites):**

| Symbol | Consumers | Check type | Correction |
|---|---|---|---|
| `GateOutcome` returned by `evaluate_gates`, consumed by `apply_promotions` | AST-verified data-flow within `helpers.py` | contract (not "import") | Original "import, helpers.py" self-file check was vacuous — corrected to a real-usage contract check. |
| `evaluate_gates` | called from `compute_verdict` | call | — |
| `apply_promotions` | called from `compute_verdict` | call | — |
| `persist_verdict_ledger` | called from `save_doc_meta` in `storage.py` | call (same-file call-site check, meaningful) | — |
| `read_verdict_ledger` called in `_persist_flat_result` | `client.py` | call | **Split from the original single generic entry** — flat path. |
| `read_verdict_ledger` called in `_persist_tree_result` | `client.py` | call | **Split from the original single generic entry** — tree path. Original combined both into one check, which would pass even if only one call site were wired, missing a partial-wiring bug. |

**Test requirements:**
- `tests/test_zone4_gate_outcome.py` — `evaluate_gates` correctness across every gate defect type: `GARBLING`→hard_fail, `NODE_COUNT_LOW`/`DEPTH_LOW`/`NODE_GARBLING`→correct defect enum, `FLAT_GATE_SUBSET` fires only flat_applicable gates, zero-content→FAIL, co-firing tiebreak via `_GATE_PRIORITY`.
- `tests/test_zone4_apply_promotions.py` — each promotion rule in isolation: image_standalone dispatch, image-enrichment rescue before `max_leaf_ratio`, base PASS/structural_ok, category promotions, small-doc exemption, MARGINAL fallback, `source_selection=True` skip.
- `tests/test_zone4_verdict_decomposition_regression.py` — identical `(verdict, reason)` output pre/post decomposition across a full input matrix.
- `tests/test_zone4_verdict_ledger.py` — `persist_verdict_ledger`/`read_verdict_ledger` round-trip; max-priority-wins guard; MinIO-unavailable graceful degradation; `wipe_processed` does not delete `verdicts/`; survives full corpus reingestion.
- `tests/test_zone4_ledger_wiring.py` — `save_doc_meta` calls `persist_verdict_ledger` conditionally on CAS guard; `_persist_flat_result` AND `_persist_tree_result` both independently call `read_verdict_ledger` (two distinct assertions, not one).
- `tests/test_zone4_threshold_coupling.py` — **Correction:** verify the single assertion lives in `PipelineConfig.from_env` (Zone 5) with correct `<=` direction; `VerdictThresholds.from_config` inherits it without a second, independent assertion.

**Corpus validation:** GHV-TKV-Tarif, Haftpflicht-Besondere, Domestic Workers doc 21, Penal Code, Federal Decree-Law 47, marsoom-13. Expected direction: **improve**. Spot-check count: 6.

**Estimated complexity:** large. **Severity:** high.

---

## Validation Results

**Overall quality:** `needs_work`. **Approved: NO.**

This plan carries 4 **blocker**-severity issues, 8 **major**-severity issues, and 5 **minor**-severity issues, surfaced by cross-zone consistency validation. All blockers stem from the same root cause: specs for later-wave zones were written against the *current* codebase surface rather than against the surface that will exist after their own declared upstream dependencies land. Blockers must be resolved — by rewriting the affected zone specs against the correct post-dependency surface — before wave execution begins. The corrected targets, constraints, and wiring checks above have been folded into each zone's Fix Spec section inline; this section is the authoritative list of what changed and why.

### Blockers (must fix before execution)

1. **Zone 4 targets symbols Zone 5 deletes.** `compute_verdict`'s dispatcher was specified to call `_get_verdict_thresholds()`, and the coupling assertion was specified to go "in `VerdictThresholds.from_env()`" — but Zone 5 deletes `_get_verdict_thresholds`/`_verdict_thresholds_cache`/`reset_verdict_thresholds` and converts `from_env` into `from_config(pipeline_config)`. **Fix applied above:** Zone 4's `helpers.py` targets now read `pipeline_config` via `VerdictThresholds.from_config`, and no longer duplicate the coupling assertion.

2. **Inverted assertion direction between Zone 4 and Zone 5.** Zone 5's original target said `assert pass_max_leaf_ratio >= leaf_split_ratio`; Zone 4's mechanism/test text implies the bad state is `PASS_MAX > LEAF_SPLIT`, i.e. the correct assertion is `<=`. If both land as originally written, any non-equal configuration fails one assertion or the other. **Fix applied above:** Zone 5's assertion corrected to `pass_max_leaf_ratio <= leaf_split_ratio`; Zone 4 now references that single assertion instead of re-adding one.

3. **Zone 2 targets code Zone 1 deletes.** Zone 2 (wave 3) targeted `_recover_ocr_retry` at `client.py:1328-1570` and constrained "the three `OcrRetryReason` branches ... must retain their independent flag gates" — but its own declared dependency Zone 1 (wave 2) deletes `OcrRetryReason` entirely and replaces `_recover_ocr_retry` with three split methods behind `_execute_ocr_retry`. **Fix applied above:** Zone 2's `client.py` recovery target now stamps `full_page_already_applied` inside `_execute_ocr_retry` (Zone 1's construct), with no `OcrRetryReason` reference.

4. **Same-wave dependency cycle.** Zone 1 lists Zone 3 as a `depends_on`, but both are scheduled in wave 2 (parallel) — a same-wave dependency is invalid. **Fix applied above:** wave 2 is now marked **sequential** (Zone 3 → Zone 1), not parallel, until the plan is re-validated with either that resequencing or a correction dropping the (apparently real) dependency edge.

### Major issues (must fix before execution)

5. **Zone 3 / Zone 5 env-var double ownership.** Zone 5 absorbs `GARBLE_NODE_RATIO_THRESHOLD`/`GARBLE_SHORT_TEXT_DEFAULT`/`GARBLE_FLAT_MARKDOWN_NORMALIZE` into `PipelineConfig` and asserts no double-reads exist; Zone 3's `GarbleConfig.from_env()` as originally specified re-reads the same vars, violating that guard. **Fix applied above:** `GarbleConfig.from_config(pipeline_config)` replaces the second `from_env` reader.

6. **Zone 3 stale reference to deleted `reset_verdict_thresholds()`.** **Fix applied above:** corrected to `reset_pipeline_config()`.

7. **Zone 3 `_script_from_filename` placement contradiction.** Spec claimed the function lives in `converters.py` (it's actually `helpers.py:1654`) while also barring `script.py` from importing `helpers.py`. **Fix applied above:** added an explicit code_target moving/reimplementing `_script_from_filename` inside `script.py`.

8. **Zone 1 flag-placement circular import.** Spec had eligibility predicates move to `helpers.py` while their backing flags "stay in client.py module scope" — `helpers.py` cannot import `client.py` (reverse of the real import direction). **Fix applied above:** flags moved into `PipelineConfig` (Zone 5), predicates read `pipeline_config` directly.

9. **Zone 2 line-range inaccuracy.** `pdf_to_markdown_docling` is defined at `converters.py:3270`, not 3540-3592 (only the tail/call-site). **Fix applied above:** anchor corrected to the call-site symbol.

10. **Zone 5 missing `client.py` code_target for its own `pipeline_config` wiring_check.** No Zone 5 edit currently touches `client.py`, yet the wiring check requires it to import `pipeline_config`. **Flagged, unresolved** — a `client.py` code_target must be added (or the check dropped) before wave 1 executes; noted explicitly in Zone 5's wiring-check table above.

11. **Vacuous self-file "import" wiring checks across Zone 5, Zone 6, Zone 1, Zone 3, Zone 4.** Multiple zones list `must_be_imported_by` pointing at the very file where a symbol is *defined* — logically vacuous for an automated checker. **Fix applied above, per zone:** replaced with real cross-file or call-site/usage checks throughout each zone's corrected wiring-check table; most consequential instance is Zone 1's missing check on `GateSpec.recovery_fns` string-to-method binding (item 12 below), and Zone 6's complete absence of any `job_status.py` check for its most safety-critical change (item 13 below).

12. **Zone 1 — no wiring_check for the `recovery_fns` string-to-method binding.** The entire dispatch mechanism resolves string names via `getattr` at runtime; a typo would pass every original wiring_check yet break silently, reproducing the exact bug class this zone claims to fix. **Fix applied above:** added as its own authoritative wiring_check entry, not left to tests alone.

13. **Zone 6 — zero wiring_checks for `job_status.py`.** The `ERROR->DONE` transition change is the single most safety-critical edit in this zone and had no wiring verification at all. **Fix applied above:** added a `job_status.py` contract check plus a `late_success` consumption check in `worker.py`.

### Minor issues (should fix, non-blocking)

14. **Zone 7 / Zone 2 line-anchor collision in `helpers.py`.** Both insert a new `ExtractionState` field "after `flat_garble_unrecovered`"; Zone 7 lands first (wave 1) so Zone 2's line anchor (wave 3) will have drifted. **Fix applied above:** Zone 2's target anchored to the field name, not a line number; `RecoveryOutcome` constraint scoped to "not modified by this zone."

15. **Zone 7 `depends_on` name collision.** `"zone-3 (RecoveryOutcome... landed cfbf1a1)"` collides with this batch's in-flight "Zone 3: Garble Detection Heuristic Patchwork." **Fix applied above:** reframed as `landed:cfbf1a1`, decoupled from the in-batch Zone 3 name.

16. **Zone 5 `FeatureWiring(...)` constructor expressions used as grep-able "symbols."** Full constructor calls aren't matchable source text. **Fix applied above:** corrected to the literal names `gate_recovery_dispatch` / `rtl_decision`.

17. **Zone 5 missing wiring_checks for `VerdictThresholds.from_config` and `reset_pipeline_config`.** Two new/renamed public symbols with no completeness check. **Fix applied above:** added both.

18. **Zone 4 combined `read_verdict_ledger` wiring check.** Single generic entry couldn't catch partial wiring (only flat path done, tree path forgotten). **Fix applied above:** split into two distinct call-site checks.

*(Check-1 "no wiring check points into `tests/`" passes cleanly across all 7 zones — no action needed, noted for completeness of the review record.)*

### Recommended next step

Do not dispatch any wave against the *original* PLAN DATA as delivered. The corrected code_targets, constraints, and wiring_checks embedded inline in each Fix Spec section above supersede the original spec text and should be treated as the actual implementation contract. Re-run validation once wave 2's sequential (not parallel) ordering and Zone 5's `client.py` gap (issue #10) are resolved, before any zone-fix workflow is dispatched.
