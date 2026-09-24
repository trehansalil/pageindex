# Remediation Plan — 2026-08-12

**Audit:** audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
**Zones:** 7 of 8 (top by priority)
**Waves:** 3
**Validation status:** NOT APPROVED — needs_work (see Validation Results)

---

## Priority Scores

| Priority | Zone | Score | Severity | Bug Count | Proposal Status | Excluded |
|---|---|---|---|---|---|---|
| 1 | Tree/Flat Verdict Split | 140.4 | critical | 18 | partially_implemented | no |
| 2 | Arabic/RTL Pipeline Blindness | 65.52 | high | 14 | no_proposal | no |
| 3 | Registry Dual-Write Consistency | 28.8 | high | 8 | no_proposal | no |
| 4 | Garble Detection Fragmentation | 24.96 | critical | 16 | implemented_and_wired | no |
| 5 | Converter-Gate-Route Ordering Chain | 21.84 | critical | 14 | implemented_and_wired | no |
| 6 | ZDR/PII Egress Gap | 10.8 | high | 3 | not_applicable | no |
| 7 | Duplicated Convergent Logic | 6.0 | medium | 6 | not_applicable | no |
| — | Worker-Child Process Boundary | 4.5 | high | 5 | implemented_and_wired | **yes** (excluded from this plan; lowest score, improving trajectory) |

Notes on scoring:
- Tree/Flat Verdict Split carries a 1.3x regression-history boost — it regressed high→critical this cycle and its predecessor also regressed in the prior POST-FIX-10 delta (two consecutive regression cycles). Directly implicates CLAUDE.md Hard Rule 5.
- Arabic/RTL Pipeline Blindness also regressed this cycle, reversing the only "improved" verdict from the prior delta (Mutable ExtractionState). No simplification proposal exists — flagged as architectural, requiring a pipeline rethink rather than routine gate patches.
- Registry Dual-Write Consistency is newly surfaced (no prior-zone match), directly implicating Hard Rule 2 (right-to-erasure cascade).
- Garble Detection Fragmentation and Converter-Gate-Route Ordering Chain both show `implemented_and_wired` infrastructure (GateSpec registry, validate_feature_wirings at startup) but low multipliers mask that root causes remain unfixed underneath — Garble is the longest-stalled zone at 6+ consecutive cycles.
- ZDR/PII Egress Gap has the lowest bug count (3) but is a direct, active Hard Rule 3 violation on the two highest-volume LLM egress sites; treat as a compliance-priority override candidate despite its numeric rank.
- Duplicated Convergent Logic improved this cycle (high→medium); lowest urgency of the seven zones carried into this plan.
- Worker-Child Process Boundary is excluded from this plan: lowest score, improving trajectory, cleanest wiring status.

---

## Wave Sequence

### Wave 1
**Zones:** Tree/Flat Verdict Split, Registry Dual-Write Consistency, ZDR/PII Egress Gap

**Rationale:** Three independent zones with zero confirmed file overlaps. Tree/Flat Verdict Split (verdict.py, gates.py, indexer.py) is the highest-priority foundational fix — Registry Dual-Write and ZDR/PII Egress both build on top of the evaluate_gates/indexer.py surface it stabilizes. Registry Dual-Write Consistency (registry_mirror.py, storage/*.py, reconcile.py) is fully isolated and addresses Hard Rule 2 compliance. ZDR/PII Egress Gap (llm.py, config.py, server.py, pictures.py egress gate) is isolated and addresses Hard Rule 3 compliance. The only near-touch is converters/pictures.py (ZDR zone edits zdr_egress_gate; Garble zone in wave 2 edits detect_garble callers) but these are confirmed as different functions with no call-edge between them.

**Shared files:** none

### Wave 2
**Zones:** Garble Detection Fragmentation, Converter-Gate-Route Ordering Chain

**Rationale:** Both zones depend on wave 1's Tree/Flat Verdict Split and are prerequisites for wave 3's Arabic/RTL zone. They share `client/indexer.py`. The originally-proposed rationale (that Garble edits `_persist_flat_result` at ~720-954 while Converter edits `_convert_to_tree` at ~956+ in disjoint regions) is **contradicted by validation** (see Validation Results, "Converter-Gate-Route Ordering Chain" major finding): both zones' actual code targets collide in the index() recovery-loop region — Garble threads `script_context` through "recovery dispatch at line 1209" and edits lines 1145-1151, while Converter deletes lines 1210-1218 and 1247-1260 in the same method. **This wave must not run as blind parallel agents on indexer.py.** Sequence within the wave: land Converter-Gate-Route Ordering Chain's `finalize_gate_and_route()` refactor of the recovery loop FIRST, then rebase Garble Detection Fragmentation's `script_context` threading on top of the now-stable recovery loop. Treat the "shared_files" declaration below as a serialization order, not a parallel-safe overlap.

**Shared files:** `src/pageindex_mcp/client/indexer.py` (serialize: Converter-Gate-Route Ordering Chain lands first within this wave, then Garble Detection Fragmentation)

### Wave 3
**Zones:** Arabic/RTL Pipeline Blindness, Duplicated Convergent Logic

**Rationale:** Arabic/RTL Pipeline Blindness depends on wave 2 outputs: it needs Garble's consolidated detection and Converter's stabilized recovery-loop pipeline. Duplicated Convergent Logic's own `depends_on` names Arabic/RTL Pipeline Blindness — but both are scheduled in the **same** wave, which validation flags as a same-wave dependency violation (the wave machinery only guarantees ordering across waves, not within one). The originally-proposed shared-file overlap on `helpers/flat.py` is also **not real**: Arabic/RTL Pipeline Blindness's actual code targets are `client/indexer.py`, `script.py`, `client/recovery.py`, `converters/ocr_langs.py`, `helpers/table_stitch.py`, and `converters/headings.py` — it does not touch `converters/pipeline.py`, `helpers/tree_validation.py`, or `helpers/flat.py` at all. Only Duplicated Convergent Logic touches `helpers/flat.py`. **Recommended resolution (apply before executing this wave):** move Duplicated Convergent Logic to wave 4, OR strip its one Arabic/RTL-dependent target (the already-deferred `route_and_extract_flat` caching note at recovery.py:467-468/567-568) so its `depends_on` list can be emptied and it can run wave-3-parallel safely. Until one of those is applied, treat wave 3 as Arabic/RTL Pipeline Blindness alone, with Duplicated Convergent Logic deferred to wave 4.

**Shared files (real overlap, corrected from proposal):** `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/client/recovery.py` (both zones touch these; `helpers/flat.py` is Duplicated Convergent Logic only, not shared)

**Critical cross-zone conflict (must be resolved before wave 1 executes — see Validation Results):** Tree/Flat Verdict Split (wave 1) and Duplicated Convergent Logic (wave 3, or 4 post-fix) both independently propose extracting the *same* duplicated hysteresis block at `indexer.py:851-877` and `989-1014` — under different names, signatures, and target files (`_apply_verdict_hysteresis` in `indexer.py`, async, vs. `apply_verdict_hysteresis` in `helpers/verdict.py`, sync). **Resolution applied to this plan:** Tree/Flat Verdict Split (wave 1, lands first) owns this extraction. Duplicated Convergent Logic's corresponding code target (item C/D and its `test_verdict_hysteresis.py` / `test_hysteresis_parity.py` requirements) is dropped from its scope — see the zone's Fix Spec below for the corrected target list.

---

## Fix Specs

### Zone: Tree/Flat Verdict Split (wave 1, priority 1)

**Severity:** critical · **Estimated complexity:** medium · **Depends on:** none

**Mechanism to eliminate:** The tree-path and flat-path persistence methods (`_persist_tree_result` vs `_persist_flat_result`) compute document verdicts through fundamentally different gate evaluation pipelines but write to the same verdict sidecar/registry. The flat path at `indexer.py:842` calls `compute_verdict` WITHOUT passing `state.gate_result` (defaults to `None`), triggering the `evaluate_gates` flat branch (`verdict.py:184-193`) which re-derives signals and runs only `FLAT_GATE_SUBSET` (3 of 10 gates: GARBLING, NODE_GARBLING, REORDERED). The tree path at `indexer.py:980` correctly passes `state.gate_result`, getting all 10 gates. This means 7 hard-fail gates (EMPTY_NODE_CONTAMINATION, LOW_CONTENT_DENSITY, SUSPECT_DENSITY, BIDI_DEGRADED, RTL_REVERSAL, NODE_COUNT_LOW, DEPTH_LOW) are structurally inert on flat-routed documents. Additionally, `apply_promotions` at `verdict.py:280-283` computes `_structural_ok` differently when `validate_result` is `None` (flat) vs present (tree): flat uses `sig.node_count >= 3 and sig.depth >= 2`, while tree uses the `_all_defects` disjointness check — these can diverge. The hysteresis block is duplicated verbatim at `indexer.py:851-877` (flat) and `indexer.py:989-1014` (tree), creating independently-drifting copies. Each fix to any gate/threshold/promotion must be validated against both paths, and historically half of such fixes only land on one path.

**Strategy:** Eliminate the split by threading `state.gate_result` through the flat path so both paths run through identical gate evaluation. Extract the duplicated hysteresis block into a shared helper. Remove `FLAT_GATE_SUBSET` and the flat re-derivation branch entirely. Sequence: (D) extract hysteresis first (pure refactor, zero semantic change), (A) thread `gate_result` through the flat path, (B+C) remove the dead flat branch and `FLAT_GATE_SUBSET`, (E) remove the `flat` kwarg. This is a net deletion (-60 to -80 lines), converging two paths into one.

**Design decision (per Validation Results resolution):** the extracted hysteresis helper `_apply_verdict_hysteresis` and its `_LEDGER_PRIORITY` constant are owned by this zone and live in `client/indexer.py` (module-level constant, async helper — either a free function or a method on `CustomPageIndexClient`). Duplicated Convergent Logic (wave 3/4) must NOT re-extract this block under a different name/location; its corresponding code target is dropped.

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `client/indexer.py` | 851-877, 989-1014 | Extract duplicated hysteresis block into shared async helper `_apply_verdict_hysteresis(verdict, verdict_reason, sha256, filename, path_label) -> tuple[str, str]` | New async method/function on `CustomPageIndexClient` encapsulating the `_LEDGER_PRIORITY` dict, `read_verdict_ledger` call, comparison, override, and graceful-degradation except block. Replace both inline blocks with calls to this helper. `_LEDGER_PRIORITY` defined once at module level (currently redefined in both blocks). `path_label` ('flat'/'tree') is for logging only. | Must produce byte-identical verdict/verdict_reason values for the same inputs. Graceful-degradation behavior (continue with computed verdict on ledger read failure) must be preserved. Zero-semantic-change refactor — run full test suite and corpus scoring to confirm no verdict changes. |
| `client/indexer.py` | 842-848 | Thread `state.gate_result` through flat path's `compute_verdict` call | Change the call from `compute_verdict(flat_structure, content_class, image_enrichment_ratio=..., expected_script=..., flat=True)` to `compute_verdict(flat_structure, content_class, state.gate_result, image_enrichment_ratio=..., expected_script=..., flat=True)`. `state.gate_result` is the `TreeGateResult` from `validate_tree` (set at line 705) — same object the tree path passes at line 983. | `state.gate_result` may legitimately be `None` for non-PDF file types that skip `validate_tree` (.docx, .md). The `None` branch in `evaluate_gates` (lines 157-161) must continue to handle this. Flat-routed docs that previously escaped 7 hard-fail gates will now be subject to them — expect some PASS/MARGINAL docs to shift to FAIL (semantically correct, needs corpus validation before merge). |
| `helpers/verdict.py` | 184-193 | Remove the flat re-derivation branch in `evaluate_gates` | Delete the `if validate_result is None and flat:` block that iterates `FLAT_GATE_SUBSET` and re-derives defects — dead code once Step A guarantees `validate_result` is always passed for PDFs. | The reordered fallback at line 195 (`if validate_result is None and not flat and sig.is_reordered`) must be preserved or generalized — consider dropping the `not flat` guard since `flat=True` with `validate_result=None` should no longer occur in production. |
| `helpers/verdict.py` | 131, 382, 407 | Remove `flat: bool = False` kwarg from `evaluate_gates` and `compute_verdict` signatures | Remove the parameter from both signatures; remove the `flat=flat` pass-through at line 407. | All callers must be updated: production caller `indexer.py:842-847` (remove `flat=True`); test callers `test_compute_verdict.py:102,107`. `classify_verdict` wrapper (422-443) does not pass `flat` — no change needed. |
| `helpers/gates.py` | 474-504 | Remove `FLAT_GATE_SUBSET`, `_FLAT_APPLICABLE_DEFECTS`, and the `flat_applicable` field from `GateSpec` | Delete the derivation, assertion, and `FLAT_GATE_SUBSET` construction. Remove `flat_applicable` from `GateSpec` in `types.py:244`. Remove `flat_applicable=True` from the three `GateSpec` entries in `GATES` (lines 336, 361, 371). Remove the `FLAT_GATE_SUBSET` import from `verdict.py:17`. Remove the export from `helpers/__init__.py`. | Also update: `helpers/__init__.py` (remove from imports and `__all__`), `test_compute_verdict.py` (delete `TestFlatGateSubset`), `test_gate_table.py` (delete `test_flat_applicable_derivation`, update `GateSpec` field assertions). The assertion at 482-495 must also be deleted. |
| `helpers/types.py` | 244, 234-236 | Remove `flat_applicable` field from `GateSpec` dataclass | Delete the field and its docstring references. | Any test introspecting `GateSpec` fields (`test_gate_table.py:173`) must be updated. |
| `helpers/verdict.py` | 280-283, 246 | Unify `_structural_ok` computation in `apply_promotions` to always use the `all_defects`-based check | After Step A, `validate_result` is always provided for PDFs. Change to always use `{TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW}.isdisjoint(outcome.all_defects)`. Remove the `validate_result` parameter from `apply_promotions` entirely — no longer needed. | For non-PDF `validate_result=None` cases, `outcome.all_defects` is an empty frozenset (set at `verdict.py:161`), so `isdisjoint` returns `True`. This is acceptable since thin non-PDF docs are caught earlier by `zero_content`/hard-fail gates. Verify via full test suite. |

#### Wiring checks

| Symbol | Must be imported/called by | Check type |
|---|---|---|
| `_apply_verdict_hysteresis` | `client/indexer.py` — **note:** since this symbol is defined in `indexer.py` itself (not imported), verify via call-count (≥2 call sites, one per former duplicated block) rather than literal "import" | call |
| `_LEDGER_PRIORITY` | `client/indexer.py` — same caveat: module-level constant defined and consumed within the same file; verify single definition + ≥2 read sites, not an import edge | import (interpret as defined-once-consumed-twice) |
| `FLAT_GATE_SUBSET` | **none** (must be unimportable anywhere — negative check, mirrors Garble zone's `check_garble` pattern) | import |
| `flat_applicable` | **none** (must have zero references anywhere in `src/` after removal) | reference |

**Note on wiring-check gaps (per validation):** the original spec covered only 2 of 5+ deleted/renamed symbols. The two negative checks above (`FLAT_GATE_SUBSET`, `flat_applicable`) are added per validation finding "Tree/Flat Verdict Split — major: wiring_checks only cover 2 symbols" to catch a partial implementation that deletes the branch but leaves stray references (confirmed present today at `gates.py:474-504`, `types.py:244`, `helpers/__init__.py:110/220`, `verdict.py:17/187/399`).

#### Test requirements

- `tests/test_compute_verdict.py` — Unified gate evaluation: `compute_verdict` with a `TreeGateResult` containing EMPTY_NODE_CONTAMINATION or LOW_CONTENT_DENSITY must produce FAIL regardless of former `flat=True`. Also: `validate_result=None` (non-PDF path) still produces a valid `VerdictResult`. (contract)
- `tests/test_compute_verdict.py` — Delete/update `TestFlatGateSubset` and `TestComputeVerdictFlatMode`; replace `test_flat_true_accepted` with a test that `compute_verdict` rejects a `flat` kwarg (TypeError); replace `test_flat_true_with_treegateresult_uses_gate_result` with the unified-path equivalent. (regression)
- `tests/test_zone1_hysteresis.py` — `_apply_verdict_hysteresis`: (1) no prior in ledger → returns original; (2) higher-priority prior → returns prior; (3) lower-priority prior → returns original; (4) ledger read exception → graceful degradation, warning logged; (5) verdict_reason format `anchored_by_ledger(was={verdict}:{reason})` preserved byte-identical. (contract)
- `tests/test_gate_table.py` — Update/delete `test_flat_applicable_derivation`; remove `flat_applicable` from expected `GateSpec` field assertions. (regression)
- `tests/test_zone1_verdict_unification.py` — End-to-end: a flat-routed document (`state.ok=False`, `state.route=Route.FLAT`) with `state.gate_result.all_defects={EMPTY_NODE_CONTAMINATION, GARBLING}` must receive FAIL, not MARGINAL/PASS — the exact regression path from RFC-029 D1/D2 and Runs 7-8. (exhaustiveness)
- `tests/test_zone1_verdict_unification.py` — `_structural_ok` unification: `all_defects` containing NODE_COUNT_LOW → `_structural_ok=False` regardless of `sig.node_count`; empty `all_defects` → `_structural_ok=True`. (contract)

#### Corpus validation

- **Affected documents:** Haftpflicht (German garbled doc — previously FAIL→PASS via flat 3-gate subset missing LOW_CONTENT_DENSITY; expected to stay FAIL after unification); any flat-routed PDF with EMPTY_NODE_CONTAMINATION / LOW_CONTENT_DENSITY / SUSPECT_DENSITY in original `gate_result` (previously invisible, now correctly FAIL); small flat documents previously promoted via `small_doc_promoted` where `_structural_ok` diverged.
- **Expected verdict direction:** improve
- **Spot-check count:** 15

---

### Zone: Registry Dual-Write Consistency (wave 1, priority 3)

**Severity:** high · **Estimated complexity:** medium · **Depends on:** none in this plan (original `depends_on` cited a nonexistent "Zone 5: Worker-Child Process Boundary" — see Validation Results; treated as unenforceable and dropped)

**Mechanism to eliminate:** Two independent stores (MinIO sidecar `.meta.json` + Postgres registry) are written via separate code paths in `_upsert_registry_row` (`registry_mirror.py:55-158`) with no transactional guarantee. A mode flag `registry_verdict_authority` simultaneously controls write order, write-barrier behavior, and verdict-retry-queue drain eligibility, coupling latency tuning with safety topology. In the `postgres` path, verdict fields are written twice per call (`upsert_verdict` then folded into `upsert_doc`). The erasure cascade (`delete_doc`, `documents.py:141-305`) silently skips Postgres deletion when `registry_enabled`/`postgres_dsn` is missing without adding an `errors[]` entry (264-284) — a direct Hard Rule 2 compliance gap. The verdict-retry-queue drain in `reconcile.py:145` is gated on `registry_verdict_authority=='postgres'`, so retries enqueued during a transient Postgres outage are never drained under the default 'minio' mode.

**Strategy:** Collapse the `registry_verdict_authority` mode flag by making Postgres the sole verdict-authority path unconditionally, eliminating the branching dual-write topology. Fold `upsert_verdict` into `upsert_doc` as a single CAS-guarded SQL statement to eliminate the double verdict write. Remove the mode guard on verdict-retry-queue drain. Add `errors[]` entries for the silent registry skip in `delete_doc`. Remove the conditional write-visibility barrier from sidecar writes (sidecar becomes archival-only).

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `worker/registry_mirror.py` | 55-158 | Collapse the minio/postgres branching in `_upsert_registry_row` into one linear path | Replace 91-143 with: (1) upsert to Postgres via combined CAS SQL (merged `verdict_fields` into `upsert_doc`), (2) best-effort sidecar backfill to MinIO. Remove the `upsert_verdict` call. Keep outer try/except (150) and metrics (145-158). Retain the pool-not-ready guard (82-89) but make its verdict-retry enqueue unconditional (remove the `registry_verdict_authority=='postgres'` check at 87). | Must not break `preprocess_client.py:168-171`, which calls `_upsert_registry_row(doc_id, content_class)` without `verdict_fields`. Signature `(doc_id, content_class, verdict_fields=None)` must stay backward-compatible. |
| `config.py` | 182, 280, 289-296 | Remove `registry_verdict_authority` field and its `_VALID_VERDICT_AUTHORITY` validation | Delete the field, its assignment, and the validation block. | Must coordinate with all 3 reader files in the same commit: `registry_mirror.py:87+91`, `storage/verdict.py:232`, `reconcile.py:145`. |
| `storage/verdict.py` | 232-233 | Remove the conditional write-visibility barrier in `save_doc_meta` | Remove the `if settings.registry_verdict_authority != "postgres":` guard and the guarded `_confirm_write_visible` call. Sidecar write (`mc.put_object`, 221-227) remains; only the read-after-write barrier is removed. | Must NOT remove the write-visibility barrier from `save_doc` (`documents.py:68`) or `save_flat_doc` (`documents.py:127`) — only the sidecar barrier is removed. |
| `registry_backfill/reconcile.py` | 145-146 | Remove the mode guard on `_drain_verdict_retry_queue` | Remove the `if settings.registry_verdict_authority == "postgres":` condition so the drain runs unconditionally every reconcile tick. | `_drain_verdict_retry_queue` must remain best-effort (already true). Remaining reconcile flow after 148 unaffected. |
| `storage/documents.py` | 264, 284 | Add `errors[]` entries for silent registry skip / pool-not-ready in `delete_doc` | At 264: `errors.append('registry: skipped (registry_enabled=False or postgres_dsn missing)')`. At 284: `errors.append('registry: pool not ready, skipped Postgres row deletion')`. | `delete_doc` must remain non-raising (Property 4). New entries go into the already-returned `errors[]`. Do not change return type or add raises. |
| `registry/queries.py` | 19-122, 129-201 | Fold `upsert_verdict`'s CAS-guarded verdict columns into `upsert_doc`'s `_UPSERT_SQL` | Merge `_UPSERT_VERDICT_SQL`'s RETURNING clause into `_UPSERT_SQL`; add `RETURNING doc_id, verdict, pipeline_version, permanent_marginal, verdict_computed_at`. Change `upsert_doc` to use `pool.fetchrow` and return the winning row dict. | The `_UPSERT_SQL` CAS guards on `verdict_computed_at` (62-80) must be preserved exactly — they prevent stale-verdict regression. Per Validation Results resolution below, keep `upsert_verdict` as a deprecated thin wrapper delegating to `upsert_doc` for one release cycle, and align the wiring check accordingly (see below). |

**Resolved contradiction (per Validation Results — was a blocker):** the code-target constraint said reconcile.py should keep calling `upsert_verdict` (deprecated wrapper) for one release cycle, while the original wiring check demanded reconcile.py call `upsert_doc` directly — mutually exclusive. **Resolution applied:** reconcile.py keeps calling `upsert_verdict` (the deprecated wrapper) for this release; the wiring check below reflects that `upsert_doc` is required only in `registry_mirror.py`, not `reconcile.py`.

#### Wiring checks

| Symbol | Must be imported/called by | Check type |
|---|---|---|
| `upsert_doc` | `worker/registry_mirror.py` (reconcile.py deliberately excluded — see resolution above; it calls the deprecated `upsert_verdict` wrapper for one release cycle) | call |
| `_drain_verdict_retry_queue` | `registry_backfill/reconcile.py` | call |
| `_enqueue_verdict_retry` | `worker/registry_mirror.py` | call |
| `save_doc_meta` | `worker/registry_mirror.py`, `registry_backfill/reconcile.py`, `storage/documents.py` | call |
| `read_registry_fields` | `worker/registry_mirror.py` | call |

#### Test requirements

- `tests/test_registry_mirror.py` — Single linear path: pool available → `upsert_doc` called once (not `upsert_verdict` + `upsert_doc`), `verdict_fields` merged, `save_doc_meta` called for sidecar backfill; pool unavailable → `verdict_fields` enqueued to Redis retry queue unconditionally; metrics fire on success/failure. (contract)
- `tests/test_registry_mirror.py` — Backward compat: `_upsert_registry_row` works with `verdict_fields=None` (preprocess_client.py path), falling back to `read_registry_fields`. (regression)
- `tests/test_storage.py` — `delete_doc` `errors[]` observable: registry_enabled=False → skip entry present; pool=None → pool-not-ready entry present; success → no spurious entry. (contract)
- `tests/test_reconcile_incremental.py` — `_drain_verdict_retry_queue` runs unconditionally regardless of any mode configuration. (regression)
- `tests/test_registry.py` — `upsert_doc` RETURNING: returns winning row dict with verdict columns; CAS temporal guard preserves existing verdict when incoming `verdict_computed_at` is older; allows write when incoming is newer or existing is NULL. (contract)
- `tests/test_storage.py` — `save_doc_meta` no longer calls `_confirm_write_visible`; barrier still called for `save_doc`/`save_flat_doc`. (regression)
- `tests/test_worker.py` — `process_document_job` registry write ordering: job status set to DONE before registry write; registry write failure does not change job status. (wiring)

#### Corpus validation

- **Affected documents:** all documents in corpus — the write path changes for every ingestion.
- **Expected verdict direction:** stable
- **Spot-check count:** 10

---

### Zone: ZDR/PII Egress Gap (wave 1, priority 6)

**Severity:** high · **Estimated complexity:** medium · **Depends on:** none

**Mechanism to eliminate:** Per-call-site opt-in ZDR gating: `zdr_egress_gate` is a voluntary gate that only 2 of ~6 LLM egress sites call. The two highest-volume ingestion paths (`_run_md_to_tree`, `_run_page_index_retrying`) send full document text without any ZDR check. The `_llm_with_retry` fallback path silently reroutes to `LLM_FALLBACK_BASE_URL` (never validated against the ZDR allowlist) exactly when the primary ZDR-compliant endpoint fails — the exact condition an operator would enable the fallback for. `vlm_extract_markdown` rasterizes full PDF pages and sends them via `get_openai_client()` with no ZDR gate. This is an architectural gap — no single enforcement choke point — not an implementation bug in one call site. Directly violates Hard Rule 3.

**Strategy:** Extract a mandatory ZDR enforcement layer into the two LLM client construction points (`get_openai_client` and `_llm_with_retry`) so every outbound LLM call is gated at the transport layer rather than per call site. (1) Add `require_zdr_compliance()` validator checking both primary and fallback URLs against `_is_zdr_allowlisted` when `pii_corpus=True`, called from `_llm_with_retry` before the fallback path and from `get_openai_client` at construction. (2) Add `zdr_egress_gate` calls to the ungated `vlm_extract_markdown` and `html_to_markdown_with_images` paths. (3) Validate `LLM_FALLBACK_BASE_URL` against the ZDR allowlist at server startup alongside the existing `OPENAI_BASE_URL` check. Converts ZDR from opt-in to opt-out.

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `config.py` | 198-204 | Add `require_zdr_compliance(base_url, purpose)` and `validate_fallback_zdr()` | After `_is_zdr_allowlisted` (~203), add `require_zdr_compliance(base_url: str \| None, purpose: str) -> None` raising `RuntimeError` (with `purpose` in message) when `pii_corpus=True` and URL fails the allowlist check. Single enforcement primitive all egress sites use. | `_is_zdr_allowlisted` unchanged. Must not break the frozen `Settings` dataclass or `_ZDR_ALLOW_PATTERNS`. |
| `server.py` | 76-84 | Extend startup ZDR validation to check `LLM_FALLBACK_BASE_URL` | After the existing `_is_zdr_allowlisted(settings.openai_base_url)` check, add a second check for `LLM_FALLBACK_BASE_URL` when non-empty. Raise `RuntimeError` with a clear message on failure. | Existing `openai_base_url` check stays intact. Server must still start when `LLM_FALLBACK_BASE_URL` is unset/empty (default case). |
| `client/llm.py` | 110-118 | Gate the fallback path in `_llm_with_retry` with `require_zdr_compliance` before sending content | Before `call_fn(base_url=fallback_base_url)`, call `require_zdr_compliance(fallback_base_url, 'LLM fallback retry')`. On `pii_corpus=True` + non-ZDR fallback, `RuntimeError` propagates as `LLMTransientFailure` instead of silently sending PII to a non-ZDR endpoint. | Must not change retry logic for `pii_corpus=False`. `_llm_with_retry`'s call signature must not change (would break all `indexer.py` callers). |
| `converters/formats.py` | 376-391 | Add `zdr_egress_gate` check to `vlm_extract_markdown` before rasterized-page LLM send | At top (after imports, ~382): `from .pictures import zdr_egress_gate; allowed, api_base = zdr_egress_gate('VLM markdown extraction', doc_id=pdf_path); if not allowed: raise RuntimeError(...)`. | Must not break VLM fallback recovery callers in `recovery.py:511` / `indexer.py:789` — they already catch exceptions and fall through gracefully. |
| `converters/formats.py` | 113-143 | Add `zdr_egress_gate` check to `html_to_markdown_with_images._describe` before image-data LLM send | Before `_call()`: `allowed, _ = zdr_egress_gate('HTML image description', doc_id=path); if not allowed: return 'image'`. | Must not change return type/contract — `'image'` fallback string already the established pattern (line 156). |
| `converters/pictures.py` | 175-193 | Refactor `zdr_egress_gate` to use `require_zdr_compliance` internally | Replace inline `_is_zdr_allowlisted` check with try/except around `require_zdr_compliance`. Success → `(True, api_base)`; `RuntimeError` → `(False, api_base)` with existing log message. | Return type `tuple[bool, str \| None]` must not change. Existing callers (`_add_vlm_descriptions`, `_generate_flat_doc_description`) continue working identically. |
| `helpers/rag.py` | 31-53 | Add ZDR gate to query-path `_llm` function | At top: conditional (only when `pii_corpus=True`) `require_zdr_compliance(settings.openai_base_url, 'RAG query')`. | Must not add latency to non-PII query path. Must not change `_llm` signature/return type. |

#### Wiring checks

| Symbol | Must be imported/called by | Check type |
|---|---|---|
| `require_zdr_compliance` | `client/llm.py`, `converters/pictures.py`, `helpers/rag.py` | call |
| `zdr_egress_gate` | `converters/formats.py`, `converters/pictures.py`, `client/indexer.py` | call |

#### Test requirements

- `tests/test_zdr_egress.py` — Exhaustive coverage of all 6 LLM egress sites (`_run_md_to_tree`, `_run_page_index_retrying` via `_llm_with_retry`, `vlm_extract_markdown`, `html_to_markdown_with_images._describe`, `_add_vlm_descriptions`, `_generate_flat_doc_description`, `_llm` in rag.py): pii_corpus=True + non-ZDR URL → blocked/empty; pii_corpus=False → proceeds; pii_corpus=True + ZDR-allowlisted → proceeds. (exhaustiveness)
- `tests/test_zdr_egress.py` — `LLM_FALLBACK_BASE_URL` validation: `_llm_with_retry` raises when pii_corpus=True and fallback not allowlisted, even if primary is allowlisted; fallback still works when pii_corpus=False. (contract)
- `tests/test_zdr_egress.py` — Server startup: `_lifespan_with_scrape` raises when pii_corpus=True and `LLM_FALLBACK_BASE_URL` is a non-ZDR endpoint; passes when env var empty/unset. (contract)
- `tests/test_zdr_egress.py` — `require_zdr_compliance` contract: raises with informative message when pii_corpus=True and not allowlisted; silent no-op when pii_corpus=False or allowlisted; handles None/empty URL. (contract)
- `tests/test_zdr_egress.py` — Regression: the two previously-gated sites (`_add_vlm_descriptions`, `_generate_flat_doc_description`) still block under pii_corpus=True + non-ZDR, confirming the refactor to `require_zdr_compliance` did not regress existing protection. (regression)

#### Corpus validation

- **Affected documents:** any PII-flagged document ingested with `pii_corpus=True`.
- **Expected verdict direction:** stable
- **Spot-check count:** 3

---

### Zone: Garble Detection Fragmentation (wave 2, priority 4)

**Severity:** critical · **Estimated complexity:** large · **Depends on:** Tree/Flat Verdict Split (wave 1)

**Mechanism to eliminate:** Multiple independently-maintained garble detection code paths with different normalization (RAW_MARKDOWN vs TREE_TEXT), different Unicode range heuristics, different call-site wiring, and broken `ScriptContext` threading. The legacy `check_garble` shim (`garble.py:597-636`) rebuilds `GarbleConfig` from `os.environ` at every call, bypassing the frozen `pipeline_config`. `_garble_ratio` is byte-identical in both `tree_validation.py:167-185` and `garble.py:756-772` (same for `ocr_noise_ratio` and `hash_pipe_ratio`). `ScriptContext` is built once at `indexer.py:1150` via `ScriptContext.from_document(filename)` but its `had_presentation_forms` field is discarded: four call sites (`gates.py:90`, `pictures.py:283`, `pictures.py:401`, `indexer.py:760`) construct throwaway `ScriptContext` objects with `had_presentation_forms=False`, and 10 production `check_garble` calls use the legacy bare `expected_script` parameter which also defaults `had_presentation_forms=False`. Arabic Presentation Forms detection never fires on production documents despite the prong being correctly implemented. This zone has stalled 6+ consecutive remediation cycles per the scorecard; the GateSpec registry infrastructure is production-wired but the three generative root causes (NFKC-before-morphology ordering, expected_script self-corruption, per-doc calibration) remain unfixed underneath.

**Strategy:** Five-step consolidation: (A) Delete the 3 duplicated helper functions from `tree_validation.py`, redirect to `garble.py` canonical copies. (B) Delete the `check_garble` backward-compat shim and `_rebuild_garble_config_compat`, migrate all 10 production call sites to `detect_garble` with explicit `ScriptContext` + `GarbleConfig`. (C) Thread the computed `ScriptContext` from `index()` (`indexer.py:1150`) through `_convert_to_tree`, `_persist_flat_result`, `_persist_tree_result`, and all recovery methods, eliminating per-call-site `ScriptContext` construction with hardcoded `had_presentation_forms=False`. Modify `_gate_node_garbling` to accept `ScriptContext` instead of bare `expected_script`. (D) Merge the flat-path inline garble gate (`indexer.py:757-809`) into `GATE_TABLE`-driven evaluation via `evaluate_gates`, keeping VLM-fallback recovery in indexer triggered by gate result. (E) Confirm `_check_bidi_coherence` remains deleted.

**Sequencing note (per wave 2 resolution above):** this zone's `indexer.py` edits (lines 1145-1151, 757-809, and threading through recovery dispatch at line 1209) must be rebased on top of Converter-Gate-Route Ordering Chain's `finalize_gate_and_route()` refactor of the same recovery loop, which lands first within this wave.

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers/tree_validation.py` | 149-185 | Delete 3 duplicated functions: `ocr_noise_ratio`, `hash_pipe_ratio`, `_garble_ratio` | Delete bodies. In `TreeSignals.from_tree` (213), replace lazy import with `from .garble import BULK_PROFILE, _garble_ratio`. Update line 236 to use imported version. | `TreeSignals.from_tree` must produce identical `garble_ratio` values; `effectively_garbled` (237) unchanged. |
| `helpers/garble.py` | 571-636 | Delete `_rebuild_garble_config_compat` and `check_garble`; make `detect_garble` sole public entry | Remove both definitions and the `check_garble` export. Update `_garble_ratio` (756-772) to call `detect_garble` threading `ScriptContext.from_script_str(expected_script)` and `BlobKind.TREE_TEXT` + `_garble_config`. | `GarbleReport.__bool__` must remain the drop-in for the prior bool return of `check_garble`. Tests using `patch.dict(os.environ, ...)` must migrate to patching `_garble_config` or `GarbleConfig.from_config`. |
| `helpers/__init__.py` | 93, 97, 270 | Remove re-exports of deleted functions | Remove `check_garble` from imports and `__all__`; remove `_rebuild_garble_config_compat` import; keep `_garble_ratio` re-export from `garble.py`. | No production code outside `helpers/` should break. |
| `client/recovery.py` | 222-238 | Migrate 3 `check_garble` calls in `_execute_ocr_retry` to `detect_garble` with `ScriptContext` | Accept `script_context: ScriptContext` parameter. Replace each `check_garble(...)` with `detect_garble(_pre_text, script_context=script_context, config=_garble_config, blob_kind=BlobKind.TREE_TEXT)`. Remove `check_garble` import (37). | OCR retry win-condition logic (pre garbled AND post not) must remain identical. `_repeating_token_density` comparison (250) unchanged. |
| `client/images.py` | 131 | Migrate 1 `check_garble` call in `_attempt_tesseract_raster_recovery` | Accept `script_context` param. Replace with `detect_garble(ocr_text, script_context=script_context, config=_garble_config, blob_kind=BlobKind.RAW_MARKDOWN if profile.normalize_markdown else BlobKind.TREE_TEXT)`. Update import (23). | Recovery behavior identical: garbled OCR → None, clean OCR → text. |
| `helpers/verdict.py` | 301 | Migrate 1 `check_garble` call in `apply_promotions` (`image_enrichment_promoted` guard) | Accept `script_context: ScriptContext \| None`. Replace with `detect_garble(_promoted_text, script_context=_ctx, config=_garble_config, blob_kind=BlobKind.TREE_TEXT)`. Update import (11). | `image_enrichment_promoted` verdict must fire under identical conditions. Must land AFTER Tree/Flat Verdict Split's `evaluate_gates` refactor. |
| `helpers/tree_validation.py` | 207-254 | Update `TreeSignals.from_tree` to use `detect_garble`, threading `ScriptContext` including `had_presentation_forms` | Replace lazy import with `detect_garble, _garble_config, _garble_ratio` from `.garble` and `BlobKind` from `..script`. Replace the `check_garble(...)` call with `bool(detect_garble(flat_text, script_context=..., config=_garble_config, blob_kind=BlobKind.TREE_TEXT))`. | `TreeSignals.garbled`/`effectively_garbled` must remain semantically identical; preserve backward compat for bare `str\|None` callers. |
| `client/indexer.py` | 1145-1151, 757-809 | Thread computed `script_context` (1150) through `_convert_to_tree`, `_persist_flat_result`, `_persist_tree_result`, recovery methods | Add `script_context: ScriptContext` param to the three methods; pass through. Replace throwaway `ScriptContext` at 758-762 with the threaded param. Replace `check_garble` at 790 (VLM fallback) with `detect_garble`. Thread through recovery dispatch at 1209 via state or explicit param. | `ScriptContext.from_document(filename)` at 1150 remains the single construction point. VLM-fallback recovery block (780-808) stays in indexer (async I/O). **Must be rebased on Converter zone's recovery-loop refactor landing first within this wave.** |
| `helpers/gates.py` | 70-104 | Update `_gate_node_garbling` to accept `ScriptContext` instead of constructing a throwaway one | Add `script_context: ScriptContext \| None = None` param; pass directly to `_garble_check_nodes` when provided instead of constructing a new one at 88-92. Update `_GateFn` type alias (250-253) or thread through `validate_tree`'s gate loop (already accepts `ScriptContext` at 264). | `_GateFn` signature change affects ALL gate functions (37-247) — thread via `validate_tree`'s existing loop rather than changing every gate's signature where avoidable. |
| `converters/pictures.py` | 280-286, 398-404 | Thread `ScriptContext` from caller instead of constructing throwaway ones | Add `script_context: ScriptContext \| None = None` param to `_text_layer_has_content` and `_document_level_text_fallback`; use when provided, fall back to current behavior when `None`. | Page-level converter functions may not always have document-level `ScriptContext` available — fallback with `had_presentation_forms=False` acceptable for non-Arabic docs; document as known limitation. |

#### Wiring checks

| Symbol | Must be imported/called by | Check type |
|---|---|---|
| `detect_garble` | `client/recovery.py`, `client/images.py`, `helpers/verdict.py`, `helpers/tree_validation.py`, `client/indexer.py`, `converters/pictures.py`, `helpers/garble.py` | import |
| `ScriptContext` | `client/indexer.py`, `client/recovery.py`, `client/images.py`, `helpers/gates.py`, `helpers/tree_validation.py`, `helpers/verdict.py`, `converters/pictures.py` | import |
| `check_garble` | none (negative check) | import |
| `_rebuild_garble_config_compat` | none (negative check) | call |
| `GarbleConfig` | `client/recovery.py`, `client/images.py`, `helpers/verdict.py` | import |
| `BlobKind` | `client/recovery.py`, `client/images.py`, `helpers/tree_validation.py`, `helpers/verdict.py` | import |
| `_garble_config` | `client/recovery.py`, `client/images.py`, `helpers/verdict.py` | import |
| `GarbleReport` | all 8 `detect_garble` call sites above | isinstance (added per validation finding — verify results are consumed via `bool()`/`isinstance(GarbleReport)`, not assumed truthy) |

**Note (per validation):** the `_garble_config` wiring check originally also listed `helpers/gates.py`, but no gates.py code target introduces that import — dropped from the table above since `_gate_node_garbling`'s change is scoped to `ScriptContext` threading, not `_garble_config` usage.

#### Test requirements

- `tests/test_garble_detection.py` — All existing `check_garble` tests migrated to `detect_garble` with explicit `ScriptContext`+`GarbleConfig`. `GarbleReport.__bool__` backward compat verified. `GarbleConfig.from_config` produces identical thresholds to the old env-var-based compat shim. (regression)
- `tests/test_garble_detection.py` — `ScriptContext` threading exhaustiveness: `detect_garble` receives `had_presentation_forms=True` for Arabic text with Presentation Forms (U+FB50-FEFF); `presentation_forms` prong fires only when `True`. (contract)
- `tests/test_garble_detection.py` — `TreeSignals.from_tree` with `ScriptContext` (not bare str) preserves `had_presentation_forms` through evaluation; `garbled=True` for Arabic presentation-forms text when set. (wiring)
- `tests/test_helpers.py` — After `_garble_ratio` dedup: importing from `helpers` (re-exported from `garble.py`) matches results of the deleted `tree_validation.py` copy on known garbled/clean samples. (regression)
- `tests/test_rfc_garble_gate.py` — `_gate_node_garbling` threads `ScriptContext` (including `had_presentation_forms`) from `validate_tree` down to `_garble_check_nodes`; `TestExpectedScriptThreading` passes with `detect_garble`. (wiring)
- `tests/test_garble_detection.py` — Exhaustiveness: AST-based static check that every production `detect_garble` call site passes a `ScriptContext` (not None) and a `GarbleConfig` (not None) — no bare `expected_script` remains. (exhaustiveness)
- `tests/test_verdict.py` — `apply_promotions` `image_enrichment_promoted` guard uses `detect_garble`, respects `ScriptContext`; `test_ocr_noise_ratio_replacement` (116) still passes after import relocation. (regression)
- `tests/test_garble_detection.py` — Integration: end-to-end garble detection on a flat-path doc where `ScriptContext` built at `index()` entry is threaded through `_persist_flat_result`; the inline garble gate (757-809) produces the same verdict when driven through `evaluate_gates` after Step D. (integration)

#### Corpus validation

- **Affected documents:** Arabic T&C PDFs with Presentation Forms; Latin-gibberish CMap mojibake PDFs; scanned PDFs with thin text layers on the flat path; image-enriched flat documents checked by `apply_promotions`.
- **Expected verdict direction:** improve
- **Spot-check count:** 8

---

### Zone: Converter-Gate-Route Ordering Chain (wave 2, priority 5)

**Severity:** critical · **Estimated complexity:** medium · **Depends on:** Tree/Flat Verdict Split (wave 1)

**Mechanism to eliminate:** Three-part chain (converter selection, gate evaluation, route decision) with no shared invariant. `_reconvert_and_revalidate` (`indexer.py:326-353`) and `_recover_rtl_repair` (`recovery.py:434-440`) update `state.gate_result`/`ok`/`reason` via `validate_tree` but do NOT update `state.first_defect` or `state.route`, creating a stale-routing window. The recovery loop (`indexer.py:1210-1218`) partially compensates but only when `not state.ok and state.route == _pre_route` — if recovery converges (`ok=True`), `first_defect`/`route` remain stale, producing workaround match arms at 1247-1260. OCR escalation is gated on string-matching `'docling'` in `conv_name` (461, 492, 505, 1063) instead of a typed capability flag, so pymupdf4llm-as-primary disarms OCR escalation silently. Recovery OCR path hardcodes `pdf_to_markdown_docling` directly (`recovery.py:158`) and `state.used_converter = 'docling'` (167), permanently coupling OCR escalation to one converter regardless of configuration. This zone regressed (+1 bug) this cycle as its mechanism broadened beyond the previously-verified-wired reason-code coupling fix.

**Strategy:** Extract a `finalize_gate_and_route()` function in `types.py` as the single writer of `state.gate_result`/`ok`/`reason`/`first_defect`/`route` from a `validate_tree` result, eliminating the 3 incomplete-update sites and the stale-routing workaround match arms. Add a `supports_ocr: bool` field to converter chain tuples returned by `pdf_markdown_converters()`, replacing 4 `'docling' in conv_name` string-match gates with a typed boolean check. Move `_defect_from_reason_str` into `types.py` next to `decide_route` so all routing logic is co-located.

**Sequencing note (per wave 2 resolution above):** land this zone's recovery-loop refactor (deletions at `indexer.py:1210-1218` and `1247-1260`, and the `finalize_gate_and_route()` call sites) FIRST within wave 2, before Garble Detection Fragmentation rebases its `script_context` threading on top of it — both zones edit the same `index()` recovery-loop region, contradicting the original "disjoint regions" assumption.

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers/types.py` | 285-320 | Add `finalize_gate_and_route()` next to `decide_route` | Takes `(state: ExtractionState, vt_raw: TreeGateResult \| tuple, flat_routing_enabled: bool)`; atomically sets `state.gate_result`/`ok`/`reason`/`first_defect`/`route`. Encapsulates the 6-line pattern currently at `indexer.py:705-717` (present) but missing at `indexer.py:346-353` and `recovery.py:434-440` (absent). Import `_defect_from_reason_str` (moved here) and `decide_route`. Returns `None` (mutates in place). | Must accept both `TreeGateResult` and legacy `(ok, reason)` tuples via `__iter__`. Must NOT change `decide_route`'s own semantics. |
| `client/indexer.py` | 699-717 | Replace 6-line derivation in `_convert_to_tree` with `finalize_gate_and_route()` call | Replace 705-717 with `finalize_gate_and_route(state, _vt_raw, settings.flat_doc_routing)`. Keep `all_defects` logging (707-713) after the call. | `prepare_tree`/`validate_tree` calls unchanged. |
| `client/indexer.py` | 346-353 | Replace incomplete update in `_reconvert_and_revalidate` with `finalize_gate_and_route()` | Replace 352-353 with the call. This ADDS the previously-missing `first_defect`/`route` derivation, eliminating the stale-routing window. | `validate_tree` call (346-350) unchanged. Downstream callers (`recovery.py:195, 516`) no longer need compensating logic. |
| `client/recovery.py` | 434-440 | Replace incomplete update in `_recover_rtl_repair` with `finalize_gate_and_route()` | Replace 439-440 with the call; add import at top of file. | `validate_tree` call (434-438) and stale rtl_decision clearing (430-433) unchanged. |
| `client/indexer.py` | 1210-1218 | Remove ad-hoc re-derivation of `first_defect`/`route` in recovery loop | Delete the conditional block at 1213-1218 — dead code once `finalize_gate_and_route()` is called inside every recovery method. Keep `total_chars` (1219) and `_pre_route` capture (1207, may keep for logging). | **Must land before** Garble zone's `script_context` threading through this same recovery dispatch region. |
| `client/indexer.py` | 1247-1260 | Remove workaround match arms for stale routes that become dead code | Delete `(True, Route.REJECT) \| (True, Route.PERSIST_FAIL)` (1247-1251) and `(True, Route.FLAT)` (1253-1260) — unreachable once `ok=True` always implies `route=TREE`. Add an assertion/exhaustiveness guard after the match. | Remaining arms `(True, Route.TREE)` (1244), `(False, Route.FLAT)` (1262), `(False, Route.REJECT)` (1289), `(False, Route.TREE\|PERSIST_FAIL)` (1299) must remain. |
| `converters/pipeline.py` | 572-634 | Add `supports_ocr: bool` to converter chain tuples | Change `pdf_markdown_converters()` return type from `list[tuple[str, Callable]]` to `list[tuple[str, Callable, bool]]`. Docling entry: `('docling', pdf_to_markdown_docling, True)`. pymupdf4llm entry: `('pymupdf4llm', _pdf_to_markdown_no_pics, False)`. Update type alias (614). | Must NOT change callable signature/behavior — static metadata field only. All unpacking callers must handle the 3-tuple. |
| `client/indexer.py` | 458-520 | Replace `'docling' in conv_name` string-match with `supports_ocr` check | Unpack `(conv_name, conv_fn, supports_ocr)` from the 3-tuple. Replace the 3 occurrences (461, 492, 505) with `supports_ocr`. `state.use_remote` check at 461 stays (should be gated on `supports_ocr AND use_remote`). | OCR escalation gate at 492 must fire for ANY converter with `supports_ocr=True`, not just docling. |
| `client/indexer.py` | 1063 | Replace `'docling' in state.used_converter` string-match with capability check | Add `supports_ocr: bool = False` field to `ExtractionState` (types.py); store the winning converter's flag; use at 1063 instead of string-matching. | `meta['extraction_route']` value must remain `'remote'`/`'local'` for sidecar backward compatibility. |
| `helpers/types.py` | 157-191 | Add `supports_ocr` field to `ExtractionState` | Add `supports_ocr: bool = False` after `used_converter`. | Default `False` (non-PDF paths skip converter chain). Must not break `RecoveryOutcome.apply()`. |
| `helpers/verdict.py` | 88-100 | Move `_defect_from_reason_str` to `types.py` next to `finalize_gate_and_route`/`decide_route` | Cut from `verdict.py`, paste into `types.py`. Update re-export in `helpers/__init__.py` to import from `types`. Update direct imports in `indexer.py`. | Pure relocation — behavior/signature unchanged. All existing callers (`indexer.py:55,715,1217`; `helpers/__init__.py:208`) must continue to resolve. |

#### Wiring checks

| Symbol | Must be imported/called by | Check type |
|---|---|---|
| `finalize_gate_and_route` | `client/indexer.py`, `client/recovery.py`, `helpers/__init__.py` | call |
| `supports_ocr` | `client/indexer.py` — verify the field is read as a condition in the 3 replaced `'docling' in conv_name` sites (~461/492/505), and that `pdf_markdown_converters()`'s return arity actually changed to a 3-tuple (added per validation — original spec left the return-type change itself unverified) | call, at each of the 3 call sites; plus an arity/isinstance check on `pdf_markdown_converters()`'s return value |
| `_defect_from_reason_str` | `helpers/__init__.py`, `client/indexer.py` | import |

**Note (per validation):** the original `supports_ocr` check used a non-standard `check_type: 'dispatch'` with unclear semantics — replaced above with concrete `call` checks at the 3 specific sites plus an explicit arity check on the converter-chain return value, since there's exactly one production call site (`indexer.py:451/458`) but a partial implementation (e.g. adding `supports_ocr` as a 4th kwarg or a dict instead of extending the tuple) would otherwise pass undetected.

#### Test requirements

- `tests/test_finalize_gate_route.py` — `finalize_gate_and_route()` atomically sets all 5 fields for each `TreeDefect` variant: GARBLING → `first_defect=GARBLING, route=TREE` (RETRY_OCR policy); NODE_COUNT_LOW → `first_defect=NODE_COUNT_LOW, route=FLAT` (RAISE + flat_routing_enabled); OK → `first_defect=OK, route=TREE`; legacy `(ok, reason)` tuple → `_defect_from_reason_str` parses correctly; `flat_routing_enabled=False` changes RAISE-policy defects to REJECT instead of FLAT. (exhaustiveness)
- `tests/test_finalize_gate_route.py` — After `_reconvert_and_revalidate`, `state.first_defect`/`route` consistent with `state.gate_result` — regression test constructing stale state (GARBLING/TREE), reconverting to NODE_COUNT_LOW, asserting fresh (not stale) values. (regression)
- `tests/test_finalize_gate_route.py` — After recovery converges (`ok=True`), `state.route=TREE` and `state.first_defect=OK` — not stale RTL_REVERSAL/FLAT. (regression)
- `tests/test_finalize_gate_route.py` — Workaround match arms unreachable after wiring: property test — for every `TreeDefect d` where `decide_route(d, True)==Route.TREE`, corresponding `gate_result.ok` must be `True`; conversely `ok=True` can only produce `route=TREE` post-`finalize_gate_and_route`. (contract)
- `tests/test_converter_chain_ocr.py` — `pdf_markdown_converters()` returns 3-tuples: docling `supports_ocr=True`; pymupdf4llm `supports_ocr=False`; when `PDF_CONVERTER=pymupdf4llm`, docling still `supports_ocr=True` as secondary; chain iteration unpacks without error. (contract)
- `tests/test_converter_chain_ocr.py` — OCR escalation gates fire based on `supports_ocr`, not converter name string: `supports_ocr=True` → `force_full_page_ocr` threaded regardless of name; `supports_ocr=False` → escalation skipped regardless of name. (wiring)

#### Corpus validation

- **Affected documents:** scanned PDFs currently processed by pymupdf4llm as primary (OCR escalation silently disarmed); documents where recovery converges but had stale FLAT/REJECT route; flat-routed documents that previously escaped 7 hard-fail gates via `FLAT_GATE_SUBSET` (Tree/Flat Verdict Split dependency).
- **Expected verdict direction:** improve
- **Spot-check count:** 10

---

### Zone: Arabic/RTL Pipeline Blindness (wave 3, priority 2)

**Severity:** high · **Estimated complexity:** large · **Depends on:** Garble Detection Fragmentation, Converter-Gate-Route Ordering Chain (both wave 2)

**Mechanism to eliminate:** Latin-centric pipeline assumptions cause every Arabic-specific fix to create new interactions with Latin-centric defaults elsewhere. Five interlocking defect patterns: (1) heading injection (`_inject_arabic_structural_headings`) injects just enough headings to clear `validate_tree` depth≥2 threshold, blocking flat fallback that yields 3-5x more content; (2) OCR language detection reads FILENAME not content (`_script_from_filename` via `detect_ocr_langs`), so Arabic scans with English filenames never get `'ara'` added to Tesseract lang list; (3) `ensure_tessdata` silently falls back to `deu+eng` when Arabic tessdata unavailable instead of raising `TessdataUnavailableError` (the raise only fires when `TESSDATA_PREFIX` is set AND download is disabled); (4) `table_is_rtl` re-evaluates per-merge in `stitch_continuation_tables`, so borderline Arabic-char ratios can flip RTL/LTR mid-document; (5) flat-prefer multiplier (3.0x) is too high for Arabic docs where heading injection produces content-poor trees. The architectural root: `ScriptContext` is computed once at `index()` entry from filename only and is never enriched with content-derived signals, so all downstream subsystems either use filename-only inference or their own ad-hoc Arabic detection with inconsistent thresholds. This zone has no existing simplification proposal (architectural, requires a pipeline rethink) and regressed this cycle, reversing the only "improved" verdict from the prior delta cycle.

**Known spec issue carried forward (per Validation Results — minor, unresolved):** the mechanism description's marsoom-13 walkthrough contains unresolved self-contradictory arithmetic about whether the 3.0x default multiplier already fires (`5972 < 3*1225=3675` vs `5972 > 3675`). **Before implementing the multiplier change below, verify against `recovery.py:554-587` which condition actually blocks marsoom-13** — whether it's the multiplier threshold itself or the `ok=True` gate from heading injection — and confirm whether the 1.5x multiplier target is still needed once the heading-injection revert guard (below) is in place, or whether that guard alone resolves the case.

**Strategy:** Introduce a content-aware `ScriptContext` enrichment pass after converter output is available (post-conversion, pre-validation). Enrich `ScriptContext.from_document` with raw converter output text so content-based Arabic detection supplements filename inference. Add an Arabic-aware flat-prefer guard with a lower multiplier when `expected_script=='Arab'`. Stabilize `table_is_rtl` by computing it once per document (not per-merge). Ensure tessdata raises `TessdataUnavailableError` for non-Latin languages even when `TESSDATA_PREFIX` is unset. Gate the flat-prefer comparison behind a script-aware threshold so Arabic documents with heading-injection-inflated trees can still fall to flat.

**Correction to original wave-3 framing (per Validation Results — major):** this zone does NOT touch `converters/pipeline.py`, `helpers/tree_validation.py`, or `helpers/flat.py` — its real targets are `client/indexer.py`, `script.py`, `client/recovery.py`, `converters/ocr_langs.py`, `helpers/table_stitch.py`, `converters/headings.py`.

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `client/indexer.py` | 1145-1151 | Enrich `ScriptContext` with post-conversion content text | After `_convert_to_tree` returns `md_content`, call `ScriptContext.from_document(filename, raw_text=state.md_content)` to re-derive a content-enriched context. If it discovers Arabic content filename-only missed, update `expected_script`. ~10-line addition after `_convert_to_tree` (~718). | Must not change behavior for German/Latin docs where filename and content agree. Must not re-run when `md_content` is `None` (non-PDF paths). Must preserve existing `expected_script` when content inference returns `None`. |
| `script.py` | 896-930 | Add content-text enrichment to `ScriptContext.from_document` | Extend `from_document` to actually let `raw_text` influence `dominant_script` when filename inference returns `'Latn'`/`None` but content is Arabic. Lower the Arabic detection floor in `_infer_script` or add a secondary `AR_CHAR_RE` ratio check when `raw_text` has ≥15% Arabic characters. ~8-line change. | Must not change `dominant_script` for docs where both filename and content clearly indicate Latin. Must preserve `had_presentation_forms` detection on raw pre-NFKC text. |
| `client/recovery.py` | 554-587 | Add script-aware flat-prefer threshold for Arabic docs | In `_recover_flat_prefer`, after computing char counts, check `expected_script`; when `'Arab'`, use a lower multiplier (default 1.5x) via new env var `ARABIC_FLAT_PREFER_MULTIPLIER`. Signature change: add `expected_script` param. ~+8 lines. | Must not change behavior for Latin docs (stays 3.0x). Call site `indexer.py:1223` must be updated. **Verify the marsoom-13 mechanism note above first.** |
| `client/indexer.py` | 1223 | Update `_recover_flat_prefer` call site | `await self._recover_flat_prefer(state, filename, ext, expected_script)`. One-line change. | Must match new signature. |
| `converters/ocr_langs.py` | 86-130 | Make `ensure_tessdata` raise `TessdataUnavailableError` for non-Latin languages even when `TESSDATA_PREFIX` unset | Currently when `TESSDATA_PREFIX` empty (102-104), ALL languages assumed present. Add: when empty AND language non-Latin, verify via `shutil.which('tesseract')` + subprocess that the traineddata file exists in tesseract's default datadir. If verification fails and download disabled, raise `TessdataUnavailableError`. If impractical, at minimum log a warning + increment `TESSDATA_LATIN_FALLBACK_TOTAL`. ~15 lines. Cache the check result per language per process lifetime. | Must not break deployments where `TESSDATA_PREFIX` is intentionally unset and system tesseract has Arabic tessdata. Must not add a subprocess call on every invocation. |
| `helpers/table_stitch.py` | 39-67 | Stabilize `table_is_rtl` decision across continuation merges | Compute `rtl_decision` once on the ORIGINAL anchor before the merge loop in `stitch_continuation_tables`, pass it to `_merge_continuation_table` as a parameter instead of re-computing inside. +3 lines in `stitch_continuation_tables`, +1 param in `_merge_continuation_table`, -1 line (remove internal `table_is_rtl` call). | Must not change merge semantics for Latin-script tables (`table_is_rtl` always `False`). Must preserve existing table-stitching test assertions. |
| `helpers/table_stitch.py` | 70-90 | Pass pre-computed RTL flag into the merge loop | Before the while loop: `is_rtl = table_is_rtl(block)`. Pass to `_merge_continuation_table`; replace internal call with the passed param. | Called from `route_and_extract_flat` (`flat.py:157`) and `prepare_tree` (`tree_split.py`) — both call sites must continue to work unchanged. |
| `converters/headings.py` | 95-155 | Add content-density guard to `_inject_arabic_structural_headings` | After injection, count injected headings vs total non-empty lines. If injected headings >30% of lines AND total content (excluding headings) <2000 chars, revert the injection — doc too thin to benefit from forced hierarchy, falls through to flat extraction. Add Prometheus counter `ARABIC_HEADING_INJECTION_REVERTED`. ~15 lines. Threshold configurable via `ARABIC_HEADING_MIN_CONTENT_CHARS` env var. | Must not revert injection for docs with substantial content (full Arabic legal codes with many articles). |

#### Wiring checks

| Symbol | Must be imported/called by | Check type |
|---|---|---|
| `ARABIC_FLAT_PREFER_MULTIPLIER` | `client/recovery.py` | import |
| `_recover_flat_prefer` | `client/indexer.py` | call |
| `ARABIC_HEADING_INJECTION_REVERTED` | `converters/headings.py` — verify `.inc()` (or equivalent) is actually invoked at the revert branch, not merely defined (added per validation — a Prometheus counter defined but never incremented is the real risk here) | call |
| `ARABIC_HEADING_MIN_CONTENT_CHARS` | `converters/headings.py` | import |
| `table_is_rtl` | `helpers/table_stitch.py` | call |
| `ScriptContext.from_document` | `client/indexer.py` | call |
| `TessdataUnavailableError` | `converters/ocr_langs.py` — verify the NEW raise branch (TESSDATA_PREFIX unset + non-Latin) is added, distinct from the existing raise path (added per validation — the exception is already imported/raised today via a different, already-covered branch) | call |

#### Test requirements

- `tests/test_arabic_rtl_pipeline.py` — Script-aware flat-prefer guard: heading-injection-inflated tree (1225 chars) vs. flat (5972 chars) triggers flat-prefer with 1.5x Arab multiplier but would not with default 3.0x Latin multiplier; `state.route` becomes `Route.FLAT` for Arab script. (regression — **verify against the marsoom-13 mechanism note before writing this test**)
- `tests/test_arabic_rtl_pipeline.py` — Content-enriched `ScriptContext`: English-named PDF with Arabic content body → `dominant_script='Arab'` post-conversion, not `None`/`'Latn'` from filename-only. (contract)
- `tests/test_arabic_rtl_pipeline.py` — `ensure_tessdata` with empty `TESSDATA_PREFIX` and `'ara'`: must not silently assume Arabic tessdata exists — must verify or warn. (contract)
- `tests/test_arabic_rtl_pipeline.py` — `table_is_rtl` stability: merging 3 continuation tables uses the RTL decision computed on the original anchor for all merges, not recomputed on the evolving result. (regression)
- `tests/test_arabic_rtl_pipeline.py` — Arabic heading injection revert guard: 5 injected headings + 800 chars → reverted; 5 injected headings + 5000 chars → kept. (contract)
- `tests/test_arabic_rtl_pipeline.py` — `_recover_flat_prefer` signature accepts `expected_script` and passes through correctly for both Arab and Latn. (wiring)
- `tests/test_arabic_rtl_pipeline.py` — End-to-end: Arabic PDF with English filename processes through content-enriched `ScriptContext`, uses Arab flat-prefer multiplier, produces more content via flat than heading-injection tree. (integration)

#### Corpus validation

- **Affected documents:** marsoom-13 (Arabic legal decree); MOU MOHRE & Nafis; SLA; warid-597; qerar-106; Federal Decree-Law No. (47) of 2021; al-qarar.
- **Expected verdict direction:** improve
- **Spot-check count:** 7

---

### Zone: Duplicated Convergent Logic (wave 3 — recommend wave 4, priority 7)

**Severity:** medium · **Estimated complexity:** medium · **Depends on:** Arabic/RTL Pipeline Blindness (same-wave dependency violation — see Wave Sequence resolution above)

**Mechanism to eliminate:** Multiple independent code paths compute the same derived value (flat-block text rendering, route_and_extract_flat invocation, row_records collection) with subtly different implementations that converge on the same downstream consumer (verdict sidecar, search index, `get_document` response). When one copy is updated and others are not, the copies silently disagree. The triplicate `_flat_block_*` functions each reimplement the table→`join(row_records)` branch; `route_and_extract_flat` runs 2-3 times per ingestion with results discarded; `flat_doc_view` re-derives `row_records` on every read. This zone improved this cycle (high→medium) as prior config-drift/wiring-enforcement concerns were substantially resolved (`validate_feature_wirings` now runs at startup; `delete_doc` is production-wired via MCP tool) — lowest urgency of the seven zones carried into this plan.

**Scope correction (per Validation Results resolution — blocker, resolved):** the hysteresis-block extraction originally proposed here (code target C/D, plus `test_verdict_hysteresis.py`/`test_hysteresis_parity.py`) is a **direct duplicate** of Tree/Flat Verdict Split's own hysteresis extraction (same source lines `indexer.py:851-877`/`989-1014`, different name/signature/location). **That target is dropped from this zone entirely** — Tree/Flat Verdict Split (wave 1) owns it. This zone's scope below reflects only the non-duplicate targets.

**Deferred target (per Validation Results — major, unresolved same-wave dependency):** the `route_and_extract_flat` caching target (recovery.py:467-468/567-568) explicitly depends on the Arabic/RTL zone's recovery-mixin restructuring landing first. Since both are nominally wave 3, **this target must not be attempted until Arabic/RTL Pipeline Blindness has actually landed and been verified** — treat this zone as effectively wave 4 for that one target, or move the whole zone to wave 4 as recommended above.

#### Code targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers/flat.py` | 187-199 | Delete `_flat_block_text` (dead production code — zero production callers, test-only) | Remove the function. Update tests importing it to use `_flat_block_primary_text` (document-text use cases) or `_flat_search_text` (search-index use cases needing OCR/description). The image-block branch (196-198) is the only behavioral difference — route those callers to `_flat_search_text`, which already includes it. | Tests in `test_rfc_pipeline.py:524`, `test_rfc_storage.py:250`, `test_rfc_blocks.py:50` must be updated. Verify no production caller regresses. |
| `helpers/__init__.py` | 65, 258 | Remove `_flat_block_text` from re-exports and `__all__` | Delete the import line and `__all__` entry. | Must be done atomically with the `flat.py` deletion to avoid `ImportError`. |
| `helpers/flat.py` | 227-250 | Eliminate `row_records` re-derivation in `flat_doc_view` by reading pre-aggregated data | Check `data.get('row_records')` first — if present, use directly. Fall back to re-derivation only if the key is missing (backward compat for docs ingested before pre-aggregation). Requires `_persist_flat_result` to store `row_records` at ingestion time. | Must remain backward-compatible with flat docs already stored without a top-level `row_records` key. `flat_doc_view`'s return shape must not change. |
| `client/indexer.py` | 895-910 | Pre-aggregate `row_records` into `flat_meta` at ingestion time | In `_persist_flat_result`, after blocks are finalized (~895), compute `row_records` from blocks (same logic currently in `flat_doc_view`: iterate `role='table'` blocks, extend `row_records`). Store as `flat_meta['row_records']`. | Must not change the `flat_meta` schema in a way that breaks `save_flat_doc`. Additive key only. |
| `client/recovery.py` | 467-468, 567-568 | **DEFERRED** — `route_and_extract_flat` re-invocation caching | Not to be implemented until Arabic/RTL Pipeline Blindness lands and its recovery-mixin restructuring is verified stable. When ready: add `cached_flat_result: tuple[str, list[dict]] \| None = None` to `ExtractionState`; populate on first call in `_apply_picture_enrichment` (`images.py:185`); comparison-only recovery methods (468, 568) read from cache instead of re-invoking; invalidate on markdown mutation (`splice_figure_markers`, bidi repair, OCR re-extraction). | **DEFERRED — do not schedule until Arabic/RTL Pipeline Blindness has landed.** Cache invalidation must correctly track markdown mutations or risk stale reads. |

**Dropped target (per scope correction above):** hysteresis-block extraction (`indexer.py:851-876, 989-1014` → `apply_verdict_hysteresis` in `helpers/verdict.py`) — owned by Tree/Flat Verdict Split instead.

#### Wiring checks

| Symbol | Must be imported/called by | Check type |
|---|---|---|
| `_flat_block_text` | none (negative check — must be unimportable from `helpers` after removal) | import |
| `_flat_block_primary_text` | remains importable | import |
| `_flat_search_text` | remains importable | import |

**Dropped wiring checks (per scope correction):** `apply_verdict_hysteresis` and `_LEDGER_PRIORITY` in `helpers/verdict.py` — these conflicted directly with Tree/Flat Verdict Split's own `_apply_verdict_hysteresis`/`_LEDGER_PRIORITY` in `indexer.py` and are removed from this zone's scope.

#### Test requirements

- `tests/test_flat_block_text_consolidation.py` — `_flat_block_primary_text` returns correct text for all block roles (prose, table, kv, image), table blocks join `row_records` correctly. `_flat_search_text` produces the same table `row_records` output plus OCR text/description for image blocks (the only behavioral difference). Regression: no production path that called `_flat_block_text` breaks when using `_flat_block_primary_text` instead. (regression)
- `tests/test_flat_doc_view.py` — `flat_doc_view` uses pre-aggregated `row_records` when present; falls back to derivation when absent; produces identical output in both paths for the same input blocks. Pre-aggregation in `_persist_flat_result` produces the same list as the re-derivation logic. (regression)
- `tests/test_flat_block_text_dead_code.py` — Exhaustiveness: `_flat_block_text` is NOT importable from `helpers` after removal (`ImportError`); `_flat_block_primary_text`/`_flat_search_text` remain importable. (exhaustiveness)

**Dropped test requirements (per scope correction):** `test_verdict_hysteresis.py`, `test_hysteresis_parity.py` — owned by Tree/Flat Verdict Split.

#### Corpus validation

- **Affected documents:** all flat-routed documents (content_class in flat_table, flat_kv, flat_prose, flat_mixed); all tree-routed documents with prior verdict ledger entries (relevant only to the now-dropped hysteresis target — retained here for reference).
- **Expected verdict direction:** stable
- **Spot-check count:** 5

---

## Excluded Zone

**Worker-Child Process Boundary** (score 4.5, high severity, 5 bugs, `implemented_and_wired`) is excluded from this remediation plan. It improved this cycle (critical→high, Δ0 bugs) with `_TERMINAL_CHILD_REASONS` now derived from `_CHILD_ERROR_REGISTRY` plus a startup exhaustiveness assertion, fully closing the manual-sync risk that drove prior findings. No regression-history boost applies (improving trajectory); lowest bug count among high-severity zones and cleanest wiring status of the eight zones — correctly deprioritized this cycle.

**Caveat:** Registry Dual-Write Consistency's original `depends_on` cited "Zone 5: Worker-Child Process Boundary Step B changes control flow reaching `_upsert_registry_row`" — this dependency is dropped from the plan (see Validation Results) since the zone is excluded here. If Worker-Child Process Boundary is revisited in a future cycle, the interaction at `job.py:341-343` (whether terminal errors raise vs. return empty string, and whether that changes if the registry write is reached) should be re-verified against whatever state Registry Dual-Write Consistency lands in.

---

## Validation Results

**Overall quality: needs_work — plan is NOT approved as originally proposed.** The issues below were surfaced by validation against the source zone specs and have been resolved in-line within the Wave Sequence and Fix Specs above; this section preserves the original findings for traceability.

### Blockers (resolved above)

1. **Duplicated Convergent Logic vs. Tree/Flat Verdict Split — hysteresis extraction collision.** Both zones proposed extracting the identical duplicated block at `indexer.py:851-877`/`989-1014` under different names (`_apply_verdict_hysteresis` async in `indexer.py` vs. `apply_verdict_hysteresis` sync in `helpers/verdict.py`), different `_LEDGER_PRIORITY` locations, and mutually exclusive wiring checks. **Resolution:** Tree/Flat Verdict Split (wave 1, lands first) owns this extraction exclusively; the corresponding target dropped from Duplicated Convergent Logic.
2. **Registry Dual-Write Consistency — contradictory `upsert_doc`/`upsert_verdict` wiring for `reconcile.py`.** The code-target constraint said `reconcile.py` should keep calling the deprecated `upsert_verdict` wrapper for one release cycle, while the wiring check demanded `reconcile.py` call `upsert_doc` directly — verified in source that `reconcile.py:42/68` currently calls `upsert_verdict`. **Resolution:** wiring check corrected to require `upsert_doc` only in `registry_mirror.py`; `reconcile.py` keeps the deprecated wrapper for this release.

### Major issues (resolved above)

3. **Registry Dual-Write Consistency — `depends_on` references a nonexistent zone.** Cited "Zone 5: Worker-Child Process Boundary" which does not exist as a scheduled zone in this plan (and is excluded above), and even if it did, it would need to land in an earlier wave than wave 1. **Resolution:** dependency dropped; documented as an unverified interaction to re-check if that zone is ever revisited (see Excluded Zone section).
4. **Duplicated Convergent Logic — same-wave dependency violation.** `depends_on: ['Arabic/RTL Pipeline Blindness']` but both were scheduled in wave 3; the wave machinery does not guarantee intra-wave ordering. **Resolution:** zone recommended for wave 4 (or its Arabic/RTL-dependent target deferred/stripped) — see Wave 3 rationale and the zone's own Fix Spec.
5. **Converter-Gate-Route Ordering Chain vs. Garble Detection Fragmentation — indexer.py collision in wave 2.** The original rationale claimed disjoint regions (`~720-954` vs. `~956+`), but both zones' actual code targets edit the same `index()` recovery-loop region (Garble threads `script_context` through "recovery dispatch at line 1209" and edits 1145-1151; Converter deletes 1210-1218 and 1247-1260). **Resolution:** intra-wave serialization enforced — Converter's `finalize_gate_and_route()` refactor lands first within wave 2, Garble's threading rebases on top.
6. **Arabic/RTL Pipeline Blindness — wave-3 rationale misdescribed this zone's actual targets.** Claimed edits to `converters/pipeline.py`, `helpers/tree_validation.py`, and `helpers/flat.py` (with `route_and_extract_flat` RTL handling) that do not appear anywhere in the zone's real code targets (which are `indexer.py`, `script.py`, `recovery.py`, `ocr_langs.py`, `table_stitch.py`, `headings.py`). **Resolution:** Wave 3 rationale and shared-files list corrected above; real overlap is `indexer.py`/`recovery.py` with Duplicated Convergent Logic, not `flat.py`.
7. **Tree/Flat Verdict Split — wiring checks cover only 2 of 5+ deleted/renamed symbols.** `FLAT_GATE_SUBSET`, the `flat_applicable` field, and the `flat` kwarg removals had no corresponding negative wiring checks (unlike Garble zone's pattern for `check_garble`). **Resolution:** two negative wiring checks added (`FLAT_GATE_SUBSET`, `flat_applicable`) to this zone's Fix Spec.
8. **Tree/Flat Verdict Split vs. Duplicated Convergent Logic — direct hysteresis conflict** (restated from Blocker #1 as a major finding in the original validation; same resolution applies).

### Minor issues (resolved or noted above)

9. **Arabic/RTL Pipeline Blindness — unresolved self-contradictory marsoom-13 arithmetic** in the mechanism description, leaving ambiguous whether the 1.5x multiplier is even needed vs. only the heading-injection guard. **Resolution:** flagged inline in the zone's Fix Spec as a required pre-implementation verification step against `recovery.py:554-587`.
10. **Tree/Flat Verdict Split — wiring checks require symbols "imported by" a file that actually defines them** (self-contradictory for a same-file design). **Resolution:** wiring-check semantics corrected in-line to "defined-once-consumed-twice" / call-count verification rather than literal import.
11. **Registry Dual-Write Consistency — "4 files" constraint off by one** (enumerated list has 3 reader files + `config.py`). **Resolution:** corrected to "3 reader files plus `config.py`" in the Fix Spec table.
12. **Garble Detection Fragmentation — `_garble_config` wiring check listed `gates.py` with no corresponding code target.** **Resolution:** dropped from the wiring-check table above.
13. **Arabic/RTL Pipeline Blindness — missing `TessdataUnavailableError` wiring check** for the new raise branch. **Resolution:** added, with a note distinguishing it from the already-existing raise path.
14. **Arabic/RTL Pipeline Blindness — `ARABIC_HEADING_INJECTION_REVERTED` wiring check type mismatch** (a Prometheus counter checked via "import" doesn't verify it's incremented). **Resolution:** changed to `call` check verifying `.inc()` at the revert branch.
15. **Converter-Gate-Route Ordering Chain — non-standard `check_type: 'dispatch'`** for `supports_ocr` with unclear semantics. **Resolution:** replaced with concrete `call` checks at the 3 specific string-match replacement sites plus an arity check on the converter-chain return value.
16. **Converter-Gate-Route Ordering Chain — no wiring check on `pdf_markdown_converters()`'s return-type/arity change itself.** **Resolution:** added as part of the `supports_ocr` wiring-check correction above.
17. **Garble Detection Fragmentation — no wiring check on `GarbleReport` consumption pattern.** **Resolution:** added `isinstance`/truthy-consumption check across all 8 `detect_garble` call sites.

### Items requiring action before wave 1 execution begins

- Apply the hysteresis-ownership resolution (Blocker #1) and the negative wiring checks (Major #7) to Tree/Flat Verdict Split before any implementation starts, since this zone lands first and other zones' scopes were already corrected against its final shape in this document.
- Confirm with the team whether Duplicated Convergent Logic moves formally to "wave 4" or stays labeled "wave 3, effectively deferred" — this plan defaults to the latter (documented in the zone's own Fix Spec) but a formal wave renumber may be cleaner for tracking.
