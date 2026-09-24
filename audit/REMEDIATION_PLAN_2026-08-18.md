# Remediation Plan — 2026-08-18

**Audit:** `audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-18_POST-FIX-7.md`
**Zones:** 5 of 6 (top by priority)
**Waves:** 3
**Validation status:** `needs_work` (approved = false) — 20 issues found, see [Validation Results](#validation-results) before executing any wave.

---

## Priority Scores

| Zone | Score | Severity | Bug Count | Proposal Status | Excluded |
|---|---|---|---|---|---|
| Dual-Store Verdict Consistency and Persistence Timing | 40.5 | high | 9 | partially_implemented | no |
| Content-Destructive Heuristics Without Safety Bounds | 33.6 | critical | 7 | no_proposal | no |
| Garble Detection Surface Fragmentation | 14.4 | critical | 12 | implemented_and_wired | no |
| OCR Recovery Pipeline Flag Conflation and Mutable State Ordering | 13.2 | critical | 11 | implemented_and_wired | no |
| Three-Layer Verdict Pipeline Implicit GATE_TABLE Coupling | 12.0 | critical | 10 | implemented_and_wired | no |
| Dead Code and Incomplete Wiring Enforcement Gap | 6.3 | high | 7 | implemented_and_wired | no |

Scoring formula: `severity_weight × bug_count × proposal_status_multiplier` (critical=4, high=3; no_proposal=1.2, partially_implemented=1.5, implemented_and_wired=0.3). Only the top 5 zones (all but "Dead Code and Incomplete Wiring Enforcement Gap") are carried into this remediation plan's waves.

Notable pattern: the three highest raw-severity zones (Garble, OCR Recovery, GATE_TABLE) score *lowest* because their consolidation interfaces are already wired — the multiplier rewards finishing small residual work over restarting from scratch. The **Content-Destructive Heuristics** zone is the outlier: `no_proposal` status with critical severity and an escalating history (high→critical this cycle) makes it the most urgent zone that currently has no dedicated remediation design — this plan drafts one for it (see its Fix Spec below) rather than deferring it further.

---

## Wave Sequence

### Wave 1
**Zones:** Garble Detection Surface Fragmentation, Dual-Store Verdict Consistency and Persistence Timing

**Rationale:** Zone 1 (`helpers.py`: `check_garble`, `garble_prongs`) and Zone 4 (`storage.py`, `registry.py`, `worker.py`) share zero primary files and have no call-chain dependency. Zone 1 must land first because `compute_verdict` (Zone 3) calls `check_garble` at hop 1 — stabilizing garble detection before the verdict pipeline is restructured. Zone 4 is the most isolated zone (storage/registry layer, no `helpers.py` or `client.py` overlap) and can safely run in parallel.

**Shared files:** none.

### Wave 2
**Zones:** Content-Destructive Heuristics Without Safety Bounds, OCR Recovery Pipeline Flag Conflation and Mutable State Ordering

**Rationale:** Zone 6 (`helpers.py`: `_gate_low_content_density`, `_strip_toc_heading_nodes`) and Zone 2 (`client.py`: `_recover_ocr_escalation`, `_recover_image_dominant_ocr`) have no direct dependency. Zone 6 must land before Zone 3 because `_gate_low_content_density` is itself a `GATE_TABLE` entry that Zone 3's promotion-rule extraction will touch. Zone 2 consolidates the recovery dispatch in `client.py` — doing this before Zone 3 gives Zone 3 a cleaner recovery interface to wire into. `helpers.py` is free from Zone 1 edits (completed in Wave 1).

**Shared files:** `src/pageindex_mcp/helpers.py` (Zone 6 lines 1762-1782/3035-3045/3187-3389/3575-3594/3626-3670; Zone 2 lines 203-231/1828-1829).

> **Conflict called out in validation (issue #4):** the wave rationale claims the two zones "share zero primary files," but `shared_files` lists `helpers.py` and both zones edit it in the same wave. Line ranges don't overlap today, but a parallel land will drift every downstream line anchor. **Resolution before executing this wave:** serialize the two zones' `helpers.py` edits (Zone 2's edits land first, since they are smaller/lower in the file; Zone 6 re-anchors by symbol name afterward), or require symbol-anchored (not line-anchored) patches for both zones' `helpers.py` targets.

### Wave 3
**Zones:** Three-Layer Verdict Pipeline Implicit GATE_TABLE Coupling

**Rationale:** Zone 3 (`helpers.py`: `compute_verdict`, `GATE_TABLE`, `GateSpec`, `GATES`) must be last. It depends on Zone 1 (`check_garble` stabilized in Wave 1), Zone 6 (`_gate_low_content_density` stabilized in Wave 2), and benefits from Zone 2's consolidated recovery dispatch (Wave 2) as the new interface it wires gates into. All three prerequisite zones editing `helpers.py` are complete, so Zone 3 can restructure `GATE_TABLE` without merge conflicts or cascading rework.

**Shared files:** `src/pageindex_mcp/helpers.py`, `src/pageindex_mcp/client.py` — all line anchors in this zone's spec were verified against the pre-wave-1 tree and **will have drifted** by wave 3 execution time (validation issue #13). Locate every Zone 3 target by symbol name, not line number.

---

## Fix Specs

### Zone: Garble Detection Surface Fragmentation (wave 1, priority 3)

**Mechanism to eliminate:** Garble detection is fragmented across two OR'd surfaces (`garble_prongs` + `_has_sparse_mojibake` at `helpers.py:1439-1440`), combined with text-self-inferred `expected_script` via the `expected_script or infer_script(text)` pattern at 10 production call sites, a dead `presentation_forms` codepoint scan at `helpers.py:1318-1326` that always returns 0 post-NFKC, and `_script_from_filename` returning `None` for German docs at `helpers.py:1548-1558`, which silently disables the `latin_gibberish` prong. Each RFC fix (023→028→029→033→034) narrowed one prong's false-positive rate while the adjacent uncovered prong or the self-inference fallback re-surfaced corruption through a different detection gap.

**Strategy:** Four incremental, independently revertible steps (kill-switches: `GARBLE_LATIN_GIBBERISH_ENABLED`, `GARBLE_SHORT_TEXT_DEFAULT`):
1. Fix `_script_from_filename` to return `'Latn'` for `deu`/`eng` filenames (currently `None`, disabling `latin_gibberish` for all German docs).
2. Fold `_has_sparse_mojibake` into `garble_prongs` as a named `sparse_mojibake` prong; simplify `check_garble` to `return bool(garble_prongs(...))` — eliminates the dual-surface split.
3. Add `had_presentation_forms: bool` parameter to `garble_prongs`, replacing the dead codepoint scan with the pre-NFKC boolean already captured on `RtlDecision.had_presentation_forms`.
4. Remove `or infer_script(text)` at all 10 call sites, making `expected_script` fully metadata-derived. Net delta: approx. −30 to −40 lines.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers.py` | 1548-1558 | Fix `_script_from_filename` German/English case | After Arabic check, add `if any(lg in langs for lg in ("deu","eng")): return "Latn"`; keep final `return None` | Must not change Arabic-filename behavior |
| `helpers.py` | 1283-1368 | Add `sparse_mojibake` prong to `garble_prongs` | Port `_MIXED_SCRIPT_RE` ratio-check logic (>0.02 threshold) from `_has_sparse_mojibake` (1461-1473) into `garble_prongs`; add `original_text: str \| None = None` param since this prong needs the un-normalized blob | Must use the ORIGINAL un-normalized text, not the garble-normalized text; regex + threshold preserved exactly |
| `helpers.py` | 1318-1326 | Replace dead presentation-forms scan | Add `had_presentation_forms: bool = False` param; replace the always-0 codepoint sum with `if had_presentation_forms: prongs.add("presentation_forms")` | Default-False behavior must be identical to current (always dead) |
| `helpers.py` | 1397-1440 | Simplify `check_garble` | Add `had_presentation_forms` param; forward `original_text=blob`; replace dual-surface OR with single `garble_prongs(...)` call | Normalize/short-circuit/BlobKind/env-gate logic preserved |
| `helpers.py` | 1461-1473 | Delete `_has_sparse_mojibake` | Remove function; keep `_MIXED_SCRIPT_RE` (still used by inlined prong) | Verify no other production caller; update `test_zone1_check_garble.py` import |
| `helpers.py` | 2337 | Remove self-inference fallback in `compute_verdict` | `expected_script=expected_script or _infer_script(_promoted_text)` → `expected_script=expected_script` | Callers must already pass metadata-derived value |
| `client.py` | 1025-1027 | Remove fallback in `_convert_to_tree` pre-garble probe | Drop `or infer_script(raw_text)` | pre_garbled flag / OCR-force gate unaffected |
| `client.py` | 468 | Remove fallback in `_attempt_tesseract_raster_recovery` | Drop `or infer_script(ocr_text)` | Tesseract-on-raster path unaffected |
| `client.py` | 1390, 1397, 1405 | Remove 3 fallbacks in `_recover_ocr_escalation` | Drop `or infer_script(...)` at each site | retry_wins heuristic / revert path preserved |
| `client.py` | 1792, 1818 | Remove 2 fallbacks in `_persist_flat_result` | Drop `or infer_script(flat_md)` / `or infer_script(vlm_md)` | VLM fallback path / `flat_garble_unrecovered` flag preserved |
| `converters.py` | 1785 | Remove fallback in `_text_layer_has_content` | Drop `or infer_script(text)` | **Highest risk**: 21 inbound callers must all thread `expected_script` from metadata first |
| `converters.py` | 1897 | Remove fallback in `_document_level_text_fallback` | Drop `or infer_script(full_text)` | RFC-024 D1 mojibake-skip logic preserved |

**Open spec gap (validation issue, must resolve before Step 4 executes):** after removing every `infer_script` fallback, hash-named uploads (e.g. `92eebefa`, `b1a72fb2` — both in this zone's own corpus_validation list) carry no filename language signal, so `_script_from_filename` still returns `None` and every script-conditional prong silently disables with no fallback at all. **Required fix before landing:** either (a) keep one centralized `infer_script` fallback inside `check_garble` itself when `expected_script is None` (single choke point, not 10 scattered call sites), or (b) resolve script from document content once at ingest (`client.py index()`) and thread that everywhere, guaranteeing no call site ever passes `None` for a hash-named upload.

**Wiring checks:**
- `_script_from_filename` called by `client.py`
- `check_garble` called by `client.py`, `converters.py`, `helpers.py`
- `garble_prongs` called by `helpers.py`
- `_has_sparse_mojibake` — must have **zero** remaining importers/callers anywhere
- `GarbleProfile` imported by `client.py`, `helpers.py`
- `BULK_PROFILE` imported by `converters.py`
- **Gap flagged in validation:** no wiring check enumerates the 10 individual `infer_script`-fallback call sites — a partial fix (e.g. 8/10 sites cleaned) would pass every check listed above while leaving self-inference live at the missed sites, which is the exact ward-597 failure mode this zone exists to eliminate. **Add before sign-off:** either one wiring check per call site (with line anchors) asserting absence of `expected_script or infer_script(`, or promote the `test_zone1_no_self_inference.py` AST-walk (already in test_requirements below) to a scored wiring check.

**Test requirements:**
- `tests/test_zone1_script_from_filename.py` — Arabic→`Arab`, German/English→`Latn`, unrecognizable→`None`; regression for Haftpflicht-style filenames (regression)
- `tests/test_zone1_sparse_mojibake_prong.py` — `sparse_mojibake` fires for `92eebefa`-pattern (21.4%), not for `b1a72fb2` legitimate transliteration (<2%); `_has_sparse_mojibake` not importable (exhaustiveness)
- `tests/test_zone1_presentation_forms_boolean.py` — fires only when `had_presentation_forms=True`; dead codepoint-scan path removed (regression)
- `tests/test_zone1_no_self_inference.py` — AST-walk of `client.py`/`converters.py`/`helpers.py` finds zero occurrences of `expected_script or infer_script(` (wiring)
- `tests/test_zone1_check_garble.py` — updated signature/import assertions (contract)
- `tests/test_zone1_garble_wiring.py` — updated AST-walk wiring assertions (wiring)
- `tests/test_zone1_latin_gibberish_german.py` — Haftpflicht-scenario Run9 FAIL→PASS flip regression (regression)

**Corpus validation:** Haftpflicht (German, latin_gibberish re-enable), ward-597 (Arabic, self-inference elimination), siyasat-hawkama (Arabic, dual-surface merge), Human-Rights (Arabic, presentation-forms boolean), 92eebefa (must still trigger sparse_mojibake), b1a72fb2 (must NOT trigger). Expected direction: **improve**. Spot-check: 6 docs.

**Estimated complexity:** medium.

---

### Zone: Dual-Store Verdict Consistency and Persistence Timing (wave 1, priority 1)

**Mechanism to eliminate:** Dual-store divergence under concurrent writes with asymmetric CAS protection. `_verdict_cas_guard` (`storage.py:515-542`) uses Python lexicographic ISO-8601 comparison on the MinIO sidecar, while `_UPSERT_SQL` (`registry.py:166-211`) uses SQL `CASE WHEN EXCLUDED.verdict_computed_at >= COALESCE(...)`. These can diverge silently. Non-verdict columns (`doc_name`, `source_url`, `node_count`, `content_class`, `sha256`, `processed_at`) are ALL unconditional last-writer-wins, so a stale reconcile-from-MinIO landing after a live dual-write silently regresses these fields. Write ordering is MinIO-first (child subprocess) then Postgres-second (parent process), creating a read-after-write race with no coordination. `_confirm_write_visible` (`storage.py:44-66`) oscillates between under- and over-provisioned delays (Run-16 `cabinet_resolution` MARGINAL→ERROR from 4.4s overcorrection).

**Strategy:** Make Postgres the single authoritative verdict store; demote MinIO sidecar to a write-behind cache. Three phases, each behind `REGISTRY_VERDICT_AUTHORITY` feature flag (default `minio`):
- **Phase 1 (additive, zero behavioral change):** add `upsert_verdict()` with `RETURNING` as the sole verdict write point; add `processed_at` CAS guard on non-verdict columns; add Redis retry-queue drain in reconcile.
- **Phase 2:** invert `worker.py` write order under `REGISTRY_VERDICT_AUTHORITY=postgres` — Postgres first via `upsert_verdict()`, then backfill MinIO sidecar from the committed row.
- **Phase 3:** after 2+ validated corpus runs, remove `_verdict_cas_guard`, remove the sidecar-only `_confirm_write_visible` call, flip the flag default, then delete the flag.

**Code targets:**

| File | Lines (corrected*) | What | How | Constraint |
|---|---|---|---|---|
| `registry.py` | after 252 | Add `upsert_verdict()` with `RETURNING` | New `_UPSERT_VERDICT_SQL` using the existing CAS pattern, returns winning row | Must not break `upsert_doc`; purely additive in Phase 1 |
| `registry.py` | 166-211 | `processed_at` CAS guard on non-verdict cols | Wrap `processed_at`/`sha256`/`node_count` in `CASE WHEN EXCLUDED.processed_at >= COALESCE(...)`; leave human-curated facet columns as last-writer-wins | Must not touch verdict-column CAS logic (191-210) or the 16-param binding (229-251) |
| `worker.py` | 684-731 | Invert write order behind flag | Under `postgres`: `upsert_verdict()` → `save_doc_meta()` backfill → `upsert_doc()`. Under `minio`: unchanged | Must keep best-effort contract (no job failure on Postgres error) — **see gap below** |
| `storage.py` | 652 | Skip `_confirm_write_visible` on sidecar path under `postgres` mode | Guard with same flag; leave `save_doc`/`save_flat_doc` barriers (220, 279) untouched | Must not remove barriers guarding artifact-body visibility |
| `storage.py` | 515-542, ~624-626* | Phase 3: remove `_verdict_cas_guard` + `_VERDICT_CAS_FIELDS` | Delete after Phase 2 validated over 2+ runs | Must not execute before Phase 2 validated; update `test_zone6_verdict_persistence.py` |
| `registry_backfill.py` | ~557/567* | Drain Redis `pageindex:verdict_retry:*` before MinIO scan | For each key: `upsert_verdict()` then `save_doc_meta()` | Best-effort; must not change existing O(delta) reconcile behavior |
| `config.py` | 39-48 area | Add `REGISTRY_VERDICT_AUTHORITY` setting | Default `minio`; valid values `minio`/`postgres`, validated at startup | Must default to `minio` for zero-risk Phase 1 deploy |

\* Line-number corrections from validation: `worker.py` verdict_fields stdout read is at **line 573**, not 605; `storage.py` `_skip_verdict` CAS branch is at **624-626** (with `_MERGE_FIELDS` at 600), not 631-636; `registry_backfill.py` `_list_meta_entries` call is at **567**, Redis setup at **557**, not 574/563.

**Blocking gap (validation issue, must fix before Phase 1 ships):** the Redis retry-queue **consumer** (drain in `registry_backfill.py`) is specified with **no producer anywhere in the plan** — grep confirms zero occurrences of `verdict_retry` in `src/pageindex_mcp/` today. Nothing ever enqueues `pageindex:verdict_retry:<doc_id>`, so the drain is dead code and the "replaces silent-loss behavior" claim is unimplementable as written. **Required addition:** a `worker.py` code target, in the Postgres-failure `except` path of the Phase-2 write-order inversion, that writes the `verdict_retry` key with the `verdict_fields` payload and a TTL.

**Related gap:** Phase 2's "Postgres-first" ordering combined with "must not fail job on Postgres failure" leaves an undefined state — if the Postgres write fails under `postgres` mode, there is no committed row to backfill the sidecar from, so the verdict lands in **neither** store (worse than today's MinIO-first path, which at least persists the sidecar). **Required fix:** on Postgres failure under `postgres` mode, fall back to writing `verdict_fields` directly to the sidecar (legacy path) AND enqueue the `verdict_retry` key.

**Wiring checks:**
- `upsert_verdict` called by `worker.py`, `registry_backfill.py`
- `_UPSERT_VERDICT_SQL` — defined and consumed within `registry.py`'s `upsert_verdict()` body (validation flagged the original "imported by registry.py" phrasing as vacuous/self-referential; treat as an intra-module usage check, not an import check)
- `registry_verdict_authority` imported by `worker.py`, `storage.py`
- **Add before sign-off:** a wiring check for the new `verdict_retry` producer path in `worker.py`, since the consumer-only check above is meaningless without it.

**Test requirements:**
- `tests/test_zone4_verdict_authority.py` — `upsert_verdict()` RETURNING semantics (contract); non-verdict CAS guard behavior (contract); write-order under `postgres` flag (wiring); write-order under `minio` flag preserves current behavior (regression); `_confirm_write_visible` skip under `postgres` (contract); Redis retry-queue drain ordering (wiring); `_UPSERT_SQL` exhaustiveness vs `_CREATE_TABLE_SQL` (exhaustiveness); CAS symmetry between SQL and new verdict CAS (contract); flag validation rejects invalid values (contract)

**Corpus validation:** cabinet_resolution_no_96, human_rights_declaration, regulatory_decision, service_level_agreement. Expected direction: **stable**. Spot-check: 6 docs.

**Estimated complexity:** large.

---

### Zone: Content-Destructive Heuristics Without Safety Bounds (wave 2, priority 2)

**Mechanism to eliminate:** Unbounded content-destructive heuristics calibrated by-incident against specific corpus documents, applied globally without pre/post safety bounds. Three independent sub-mechanisms: (1) ToC-stripping guard (`helpers.py:3575-3594`) uses coarse all-or-nothing depth/node-count thresholds — too aggressive for fine-grained legal statutes (Federal Decree-Law 47 flattened to 88% body-less fragments) and insufficiently protective for deep trees (Penal Code depth 3→2, 17% node loss, under the 20% threshold); (2) content-density gate (`helpers.py:1762-1782`) uses a single global `chars_per_node` threshold (150) with no script awareness, false-rejecting well-structured Arabic legal hierarchies; (3) table segmentation (`helpers.py:3187-3389`) uses orientation-unaware thresholds shared between landscape and portrait pages, so tuning one regresses the other (RFC-035 regressed landscape MARGINAL→FAIL and portrait PASS→MARGINAL simultaneously).

**Status note:** this zone currently has **no dedicated remediation proposal** in the audit (`no_proposal`, the reason its priority multiplier is punitive at 1.2 despite critical severity). The strategy below is drafted fresh as part of this plan, not carried over from a prior design.

**Strategy:** `TransformSafetyEnvelope` wrapper pattern — every content-destructive heuristic computes a pre/post content delta and aborts when the delta exceeds a configurable, document-characteristic-aware bound (script, content_class, orientation). Step A: char-preserving depth-aware ToC guard. Step B: script-aware content-density thresholds. Step C: orientation-aware table segmentation. Step D: extract the safety envelope as a reusable, registry-enforced wrapper. Each step independently deployable behind env-var kill-switches; no new heuristics introduced, only safety bounds on existing ones.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers.py` | 3575-3594 | Char-preserving ToC guard | Add `char_loss_ratio` check (abort >15%, env `TOC_STRIP_MAX_CHAR_LOSS_RATIO`); refine depth guard to `depth_delta>1 AND resulting_depth<2`; log char_loss_ratio | Must not break RFC-034 D16 over-strip guard tests |
| `helpers.py` | 1762-1782 | Script/depth-aware density gate | Accept `document_depth` param; tiered threshold: deep trees (depth≥4) or `expected_script=='Arab'` → floor 50 (env `RFC029_MIN_CHARS_PER_NODE_DEEP`); shallow → keep 150 | Must not weaken gate for genuinely sparse German PDFs; `node_count>=200` floor unchanged |
| `helpers.py` | 3187-3389 | Orientation-aware table segmentation | Accept `orientation` param; landscape: `min_rows` 5→10, singleton ratio 0.6→0.4 (envs `RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE`, `RFC036_SINGLETON_RATIO_LANDSCAPE`) | Must not change behavior for `orientation!='landscape'`; content-preservation invariant maintained |
| `helpers.py` | 3035-3045 | Thread `orientation` through `prepare_tree` | Add optional param, pass through | Existing callers without the param must be unaffected |
| `client.py` | 963, 1269 | Pass orientation at both call sites | Derive from `_tag_landscape_pages_for_fallback` (`converters.py:2006`) metadata | **Both** sites must be wired — see wiring-check note below |
| `client.py` | ~2475-2480 | Char-loss observability at ToC call site | Log + Prometheus counter `TOC_STRIP_HIGH_CHAR_LOSS` when ratio >0.10 (below abort threshold) | Observability only, no behavioral change |
| `helpers.py` | 3626-3670 | Fence-delimiter parity hardening | Add bounded `fence_depth` counter; warn on orphan-close or unclosed-at-EOF | Must not change RFC-030 D0 stripping behavior — observability only |

**Corrections from validation:** the orientation-source reference to `converters.py _page_orientation_info` is fictional — that symbol does not exist. Use only `_tag_landscape_pages_for_fallback` (`converters.py:2006`).

**Blocking gap (validation issue, must resolve before Step D ships):** `test_zone6_exhaustiveness.py` requires a `DESTRUCTIVE_HEURISTICS` registry list with a safety-bound check per entry, and the strategy names this as "Step D," but **no code target defines this registry or the wrapper it implies**. **Required addition:** a `helpers.py` code target defining `DESTRUCTIVE_HEURISTICS` (the three heuristics above, each paired with its safety-bound predicate) plus a wiring check requiring all three register into it — or explicitly drop Step D and the exhaustiveness test from this zone's scope if the wrapper is deferred to a follow-up.

**Wiring checks:**
- `TOC_STRIP_MAX_CHAR_LOSS_RATIO`, `RFC029_MIN_CHARS_PER_NODE_DEEP` imported by `helpers.py`
- `TOC_STRIP_HIGH_CHAR_LOSS` imported by `client.py`
- `prepare_tree` called by `client.py` **at line 963** — separate check
- `prepare_tree` called by `client.py` **at line 1269** — separate check (validation flagged a single generic per-file check as satisfiable by wiring only one of the two mandated sites, silently leaving the other on `orientation=None`)
- `_strip_toc_heading_nodes_guarded` called by `client.py`
- `_gate_low_content_density`, `_segment_table_nodes` called by `helpers.py`
- **Add before sign-off:** `RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE` and `RFC036_SINGLETON_RATIO_LANDSCAPE` imported by `helpers.py` (currently missing despite being introduced by a code target)
- **Add before sign-off:** `DESTRUCTIVE_HEURISTICS` populated by all three heuristics, if Step D proceeds

**Test requirements:**
- `tests/test_zone6_toc_guard.py` — char-loss abort thresholds, refined depth guard, env-var override (contract)
- `tests/test_zone6_density_gate.py` — script-aware thresholds (standard=150 for depth<4, deep/Arabic=50 for depth≥4 — note: fix the swapped labeling in the original test description before writing this test), `node_count<200` bypass preserved (contract)
- `tests/test_zone6_table_segment_orientation.py` — landscape vs portrait threshold divergence, `orientation=None` preserves existing behavior exactly (contract)
- `tests/test_zone6_prepare_tree_orientation.py` — orientation threading correctness (wiring)
- `tests/test_zone6_fence_observability.py` — fence-parity warnings, zero content loss in all cases (regression)
- `tests/test_zone6_toc_char_loss_logging.py` — `TOC_STRIP_HIGH_CHAR_LOSS` fires >0.10, silent below (regression)
- `tests/test_zone6_exhaustiveness.py` — only in scope if `DESTRUCTIVE_HEURISTICS` registry is built (exhaustiveness)

**Corpus validation:** uae_penal_code, federal_decree_law_no_33, federal_decree_law_no_47, marsoom_13, qerar_106, sla_agreement, mou_document, world_stats_pocketbook, cabinet_resolution_no_96, haftpflicht, reitlehrer. Expected direction: **improve**. Spot-check: 11 docs.

**Estimated complexity:** medium. **Depends on:** Garble Detection Surface Fragmentation (wave 1).

---

### Zone: OCR Recovery Pipeline Flag Conflation and Mutable State Ordering (wave 2, priority 4)

**Mechanism to eliminate:** Three-fold bug generator: (1) **Flag conflation** — `OCR_ESCALATION_GARBLE` gates both Recovery 1 (`client.py:1315`, page-level garble retry) and Recovery 5 (`client.py:1624`, image-dominant structural retry), so toggling one silently disables the other. (2) **Implicit mutable state ordering** — `ExtractionState` (`helpers.py:203-231`) is mutated in-place with no return value; Recovery 1 flipping `state.ok=True` short-circuits Recovery 5's `!state.ok` gate; Recovery 1 clears `state.rtl_decision` only on the remote path (line 1364) while Recovery 5 clears it unconditionally (line 1679). (3) **Dual dispatch** — the gate-driven loop (`client.py:2197-2204`) handles `ocr_escalation`/`rtl_repair` by tag, but image-dominant recovery lives post-loop as ad-hoc code because `NODE_COUNT_LOW`/`DEPTH_LOW` gates have no `recovery_tag`. Additionally, `_repeating_token_density` returns `None` for <20 tokens, making the density comparison unreachable for no-text-layer PDFs (RFC-029 D4 bug — OCR retry always reverts). Language-derivation and OCR-dispatch code is duplicated verbatim between the two recovery methods.

**Strategy:** Consolidate both recovery methods into a single `_recover_ocr_retry` accepting a typed `OcrRetryReason` enum (`GARBLE`, `LOW_CONTENT`, `IMAGE_DOMINANT`). Decouple the flag gate into independent per-reason checks. Fix `_repeating_token_density` to return `1.0` instead of `None`. Unify language derivation, OCR dispatch, picture-splice, and `rtl_decision` clearing into the one method. Add `recovery_tag="ocr_escalation"` to `NODE_COUNT_LOW`/`DEPTH_LOW` GateSpecs so image-dominant recovery enters the gate-driven loop. Three sub-waves: (1) zero-behavioral-change flag split + density fix, (2) method consolidation, (3) re-entry guard on per-picture OCR.

**Code targets:**

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| `helpers.py` | 203-231 | Add `OcrRetryReason` enum | `StrEnum` with GARBLE/LOW_CONTENT/IMAGE_DOMINANT | `ExtractionState` fields unchanged; no circular imports |
| `config.py` | 39-48 | Add independent `IMAGE_DOMINANT_OCR_ESCALATION` flag | New config-level flag, default `True` | `OCR_ESCALATION_GARBLE` default stays `True`; `OCR_ESCALATION_PER_PICTURE` unrelated/unchanged. **See env-var naming note below.** |
| `client.py` | 1295-1458 | Replace Recovery 1 with unified `_recover_ocr_retry` | Per-reason independent flags; single language-derivation block; single OCR-dispatch block; **always** clear `state.rtl_decision` before revalidate; keep-best heuristic only for GARBLE/LOW_CONTENT | `RecoveryOutcome` pre-retry snapshot preserved for GARBLE/LOW_CONTENT; `_reconvert_and_revalidate` interface unchanged |
| `client.py` | 1611-1693 | Delete Recovery 5 | Logic absorbed into `_recover_ocr_retry(reason=IMAGE_DOMINANT)` | Metric label `still_image_only` must survive |
| `client.py` | 1376-1382 | Fix `_repeating_token_density` | Return `1.0` instead of `None` for <20 tokens | 0.80 multiplier threshold unchanged |
| `client.py` | 2172-2224 | Update dispatch table; delete post-loop ad-hoc call | Add IMAGE_DOMINANT to `ocr_escalation` dispatch list; remove lines 2221-2236 | Tag-dedup (`_seen_tags`) and post-loop quality checks (`_recover_flat_prefer`, `_recover_landscape_reroute`) must NOT move |
| `helpers.py` | 1828-1829 | Add `recovery_tag='ocr_escalation'` to NODE_COUNT_LOW/DEPTH_LOW | GateSpec update | Import-time assertion at 1859-1865 needs updating for RAISE-policy gates with recovery_tag |
| `client.py` | 415-417 | Delete local env-var read | Import canonical flag from `config.py` | See naming note below |
| `converters.py` | ~2620-2625 | Re-entry guard on per-picture OCR | Add `force_full_page_ocr_applied: bool = False` param to `_recover_picture_results`; return `[]` when `True` | Per-picture path must still fire on initial conversion |

**Naming contradiction (validation issue, must resolve before landing):** the `config.py` target says the new flag reads env var `IMAGE_DOMINANT_OCR_ESCALATION`, while the `client.py:415-417` target's constraint says the existing/must-remain-supported name is `IMAGE_DOMINANT_OCR_ESCALATION_ENABLED`. **Resolution:** `config.py` reads `IMAGE_DOMINANT_OCR_ESCALATION_ENABLED` (the existing name) as primary; update the `config.py` target's implementation text to match.

**Minor gap flagged in validation:** the LOW_CONTENT reason is specified to check `OCR_ESCALATION_GARBLE` (the same flag as GARBLE), which partially re-creates the flag-conflation mechanism this zone exists to eliminate. Decide explicitly: either document that GARBLE and LOW_CONTENT intentionally share the flag as one escalation family, or give LOW_CONTENT its own independent flag for full decoupling.

**Wiring checks:**
- `OcrRetryReason` imported by `client.py`; `OcrRetryReason.GARBLE`/`LOW_CONTENT`/`IMAGE_DOMINANT` dispatched in `client.py`
- `IMAGE_DOMINANT_OCR_ESCALATION` imported by `client.py`
- `_recover_ocr_retry` called by `client.py`
- `effective_config_snapshot` called by `worker.py`
- **Add before sign-off:** `_recover_ocr_escalation` and `_recover_image_dominant_ocr` — both must have **zero** remaining callers/references after deletion (mirroring the `_has_sparse_mojibake` pattern in Zone 1)
- **Add before sign-off:** `_recover_picture_results` (or its new `force_full_page_ocr_applied` param) called by `converters.py` with the flag threaded from the `force_full_page_ocr` context — currently only covered by a test requirement, not a wiring check

**Test requirements:**
- `tests/test_zone2_ocr_recovery.py` — enum exhaustiveness (exhaustiveness); independent per-reason flag gating (contract); unconditional `rtl_decision` clear regardless of path (regression, closes Recovery-1 bug); `_repeating_token_density` returns 1.0 not None (regression, RFC-029 D4); IMAGE_DOMINANT skips keep-best (contract); image-line-ratio entry guard (contract); pre-retry snapshot only for GARBLE/LOW_CONTENT (contract); single language-derivation block via AST inspection (contract); per-picture re-entry guard (contract); config.py canonical flag source (wiring)
- `tests/test_zone3_recovery_pipeline.py` — NODE_COUNT_LOW/DEPTH_LOW recovery_tag wiring (wiring); Recovery 5 deletion confirmed, no post-loop ad-hoc call (wiring)

**Corpus validation:** وارد-597, القرار-التنظيمي, سياسة-حوكمة, Haftpflicht, world-stats-pocketbook. Expected direction: **improve**. Spot-check: 5 docs.

**Estimated complexity:** large. **Depends on:** Garble Detection Surface Fragmentation (wave 1).

---

### Zone: Three-Layer Verdict Pipeline Implicit GATE_TABLE Coupling (wave 3, priority 5)

**Mechanism to eliminate:** `GATE_TABLE` list position implicitly encodes severity rank via `_GATE_PRIORITY = {defect: idx for idx, ... in enumerate(GATE_TABLE)}` — reordering the list silently changes tiebreaks, primary-defect selection, and recovery-tag dispatch order. Adding a new `GateSpec` requires simultaneously updating 5 coupled sites: list position, `REASON_POLICY` completeness, `HARD_FAIL_DEFECTS` membership, `client.py`'s recovery_tag dispatch dict, and `compute_verdict`'s promotion/exemption ordering (image-enrichment rescue must fire BEFORE `max_leaf_ratio` hard-fail, locked by RFC-022 B2). `_FLAT_APPLICABLE_DEFECTS` is a standalone hardcoded frozenset that must be manually kept in sync with `GATES`. `compute_verdict`'s Phase 2 promotion branches (complexity 28, 226 lines) are inline first-match-wins branches, untestable/unauditable in isolation.

**Strategy:** Four independently deployable, zero-corpus-verdict-change steps: (A) explicit `severity: int` field on `GateSpec`, derive `_GATE_PRIORITY` from it instead of `enumerate()`. (B) explicit `flat_applicable: bool` field, derive `_FLAT_APPLICABLE_DEFECTS`/`FLAT_GATE_SUBSET` from it. (C) promote the per-call `recovery_tag` assertion to module-level import-time. (D) extract `compute_verdict` Phase 2 into a `PromotionRule` registry of 7 named, independently testable pure functions — reduces `compute_verdict` from ~226 lines/complexity 28 to ~80 lines/complexity ~8.

> **Note (validation):** the strategy text says "6 promotion branches" while both the code target and the test requirement enumerate **7** functions ending in `_promote_small_doc`. Use 7 as authoritative.

**Code targets:**

| File | Lines (pre-wave-1 anchor — locate by symbol, not line number, per wave-3 caveat) | What | How | Constraint |
|---|---|---|---|---|
| `helpers.py` | ~242-262 | Add `severity`/`flat_applicable` fields to `GateSpec` | Both with defaults so existing constructor calls remain valid | `GateSpec` stays `frozen=True`; existing field order unchanged |
| `helpers.py` | ~1826-1842 | Set severity/flat_applicable per gate | severity 0-9 matching current list positions (GARBLING=0 ... SUSPECT_DENSITY=9; dead/OK=99); flat_applicable=True only for GARBLING/NODE_GARBLING/REORDERED | `GATES` list order itself must not change |
| `helpers.py` | ~1874-1877 | Derive `_GATE_PRIORITY` from severity field | `{g.defect: g.severity for g in GATES if g.gate_fn is not None}` + import-time uniqueness assertion | Must produce identical key-values to current enumerate-based derivation |
| `helpers.py` | ~1882-1895 | Derive `_FLAT_APPLICABLE_DEFECTS` from flat_applicable field | `frozenset(g.defect for g in GATES if g.flat_applicable)` | Result must equal current hardcoded `{GARBLING, NODE_GARBLING, REORDERED}` |
| `client.py` | ~2190-2195 | Promote recovery_tag assertion to import time | Module-level `_EXPECTED_RECOVERY_TAGS` frozenset + assertion; keep the per-call assertion as defense-in-depth; guard with `PAGEINDEX_SKIP_GATE_ASSERTIONS` bypass | Must crash at import if a new recovery_tag lacks a dispatch entry |
| `helpers.py` | ~2186-2411 | Extract `PromotionRule` registry | 7 named pure functions (`_promote_image_enrichment`, `_reject_max_leaf_ratio`, `_promote_base_pass`, `_promote_cat_a`, `_promote_cat_b_flat`, `_promote_cat_c`, `_promote_small_doc`) + `PROMOTION_RULES` ordered list; import-time assertion that image_enrichment's index < max_leaf_ratio's index (RFC-022 B2 lock) | Must preserve first-match-wins short-circuit semantics; must produce identical verdicts on all 25 corpus docs; extract one rule at a time across sub-PRs |
| `preprocess_client.py` | ~220-374 (real: 221/227-228/321-326) | Verify `recompute_verdicts` compatibility | No code change — spot-check assertion only | Must produce identical verdicts before/after refactor |

**Unresolved detail (validation issue):** the `PromotionRule` loop prose references `_apply_clamp(result)` as part of the new Phase 2 loop, but `_apply_clamp` has no code target and is not clear as pre-existing vs. new. **Clarify before implementation:** if `_apply_clamp` already exists, cite its current location; if new, add a proper code target and wiring check for it.

**Wiring checks:**
- `GateSpec.severity`, `GateSpec.flat_applicable` — field-access checks in `helpers.py` comprehensions (validation notes `dispatch` is the wrong check_type for a non-branching data field; use a field-usage/attribute check)
- `_EXPECTED_RECOVERY_TAGS` imported by `client.py`
- `PromotionRule`, `PROMOTION_RULES`, and all 7 `_promote_*` functions — defined and consumed within `helpers.py`; treat as intra-module usage checks, not "imported by helpers.py" (self-import is vacuous, per the same pattern flagged in Zone 4)
- **Add before sign-off:** resolve the `_apply_clamp` ambiguity above with either a code target or a note that it's pre-existing

**Test requirements:**
- `tests/test_zone3_gatespec_severity.py` — severity uniqueness/positional match to current enumerate order; `_GATE_PRIORITY` invariance under list reordering; duplicate-severity import-time failure (exhaustiveness); `flat_applicable`-derived frozenset correctness; auto-sync on new gate addition (exhaustiveness); module-level recovery_tag assertion + bypass env var (contract)
- `tests/test_zone3_promotion_rules.py` — 7-entry registry in correct order; RFC-022 B2 ordering lock; independent callability; unique labels (contract); per-rule isolation (return-None conditions) (regression); `compute_verdict` complexity-reduction regression vs existing parametrized cases, hard-fail tiebreak, masked-defect resolution (regression)
- `tests/test_zone3_recovery_pipeline.py` — existing 526-line suite passes unchanged for Steps A-C (regression)
- `tests/test_zone1_gate_table.py` — existing gate-table completeness tests pass unchanged (regression)

**Corpus validation:** all 25 corpus documents (full regression required for a verdict-affecting refactor), with named focus on Penal Code, Human Rights, federal_decree_law_no_33, marsoom-33, world-stats-pocketbook, cabinet_resolution_no_96, ward-597, SLA doc. Expected direction: **stable**. Spot-check: 25 docs.

**Estimated complexity:** large. **Depends on:** Garble Detection Surface Fragmentation, Content-Destructive Heuristics Without Safety Bounds, OCR Recovery Pipeline Flag Conflation and Mutable State Ordering (all prior waves).

---

## Validation Results

**Overall quality:** `needs_work`. **Approved:** false. 20 issues found (4 major, 1 blocker, remainder minor) across the 5 zones in scope. None of the issues invalidate a zone's core mechanism/strategy — all are spec-completeness gaps (missing producers for specified consumers, missing wiring checks, line-number drift, naming contradictions, or fictional symbol references) that must be closed before a wave starts executing, not before this plan is written. They have been folded inline into each zone's Fix Spec above as "blocking gap" / "gap flagged in validation" / "correction from validation" call-outs rather than left as a separate unread appendix.

**Blocker (must fix before Content-Destructive Heuristics wave 2 executes):** `test_zone6_exhaustiveness.py` asserts behavior of a `DESTRUCTIVE_HEURISTICS` registry that no code target creates — either build the registry (Step D) or drop the test and Step D from scope.

**Majors (must fix before their respective wave executes):**
1. Dual-Store zone: Redis `verdict_retry` consumer specified with no producer anywhere in the codebase or the plan — dead code as written.
2. OCR Recovery zone: `IMAGE_DOMINANT_OCR_ESCALATION` vs `IMAGE_DOMINANT_OCR_ESCALATION_ENABLED` env-var naming contradiction between two code targets.
3. Content-Destructive zone: Wave 2's "zero shared files" rationale contradicts its own `shared_files: [helpers.py]` field; requires serialized edits or symbol-anchored patches within the wave.
4. Garble Detection zone: Step 4 removes the only script-inference fallback with no defined behavior for hash-named documents that have no filename language signal — including two documents in this zone's own corpus_validation list.
5. Content-Destructive zone: the two `prepare_tree` call sites (client.py:963 and :1269) are covered by one generic wiring check that a partial fix could satisfy while leaving one site un-wired.
6. Garble Detection zone: 10 `infer_script`-fallback removal sites are covered only by generic per-file wiring checks, not per-site — same partial-fix risk as above.
7. OCR Recovery zone: the `_recover_picture_results` re-entry-guard threading has a test requirement but no wiring check.

**Minors:** several line-number inaccuracies (corrected inline above), vacuous "imported by own defining module" wiring-check phrasing (Dual-Store, GATE_TABLE zones), a fictional `converters.py` symbol reference (`_page_orientation_info`), a swapped-labels test description (density-gate thresholds), an off-by-one in prose ("6" vs "7" promotion branches), and a `check_type: dispatch` misclassification for plain dataclass fields.

**Recommendation:** do not begin wave execution on any zone until that zone's blocking/major gaps (called out inline in its Fix Spec above) are closed by the implementing agent or explicitly waived by the user. Minors may be fixed opportunistically during implementation without re-gating the wave.
