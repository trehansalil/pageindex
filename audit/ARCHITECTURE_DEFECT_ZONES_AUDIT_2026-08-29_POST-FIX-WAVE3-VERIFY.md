# Architecture Defect Zones Audit — 2026-08-29 POST-FIX-WAVE3-VERIFY

**Date:** 2026-08-29
**Run:** POST-FIX-WAVE3-VERIFY
**Scope:** Six critical and high-severity defect zones identified across verdict gating, detection/recovery dispatch, converter chain management, OCR pipeline orchestration, content measurement, and verdict persistence.

## Summary Table

| # | Zone | Severity | Bug Count | Key Impact |
|---|---|---|---|---|
| 1 | Verdict Promotion / Threshold Ratchet | Critical | 6 | Threshold widening fixes false-FAIL but unmasks false-PASS; promotion paths bypass structural checks |
| 2 | Detection-Remediation Dispatch Gap | Critical | 4 | Garble detected correctly but recovery escalation fails to connect; early-exit gates prevent remediatio |
| 3 | Converter Chain / Remote Service Boundary Drift | High | 4 | Local fixes never reach production; remote Docling service independently versioned with no skew enforcement |
| 4 | OCR Pipeline Conflation | High | 3 | Per-picture OCR duplicates work during force_full_page_ocr; standalone images bypass enrichment splice |
| 5 | Content Measurement Blind Spot | High | 3 | block.get('text','') returns 0 chars for tables; affects both pipeline and audit harness equally |
| 6 | Verdict Persistence Asymmetry | Medium | 2 | Three writers with asymmetric consistency; erasure manifest manually maintained and drifts out of sync |

---

## Zone Details

### Zone 1: Verdict Promotion / Threshold Ratchet

**Severity:** Critical | **Bug count:** 6

#### Mechanism

The `apply_promotions` function (verdict.py:404-571) houses an ordered if/elif promotion pipeline (D2) gated by a structural hard-fail check (D1). Every threshold widening intended to fix one class of false-FAIL systematically unmasks a new class of false-PASS. Each threshold edit invalidates test fixtures calibrated to the prior value. Promotion paths (`image_enrichment_promoted`, `cat_b_promoted`, `cat_c`, `small_doc`) can elevate near-zero-content or garbled documents to PASS when the promotion reason is treated as sufficient regardless of actual content quality. The `max_leaf_ratio` metric alone is blind to document-order violations, heading-rank inversions, and structural corruption, so structurally corrupt documents pass the single numeric gate.

The promotion pipeline is an ordered priority cascade where a single match wins. Widening any threshold (e.g. `PASS_MAX_LEAF_RATIO` 0.17 to 0.30) fixes false-FAILs for legitimate borderline documents but simultaneously admits low-quality documents that were correctly rejected at the prior threshold. The `image_enrichment` promotion path further compounds this by granting `source_selection` bypass of the `_clamp_pass` bidi/depth caps — so the structural caps that gate every other promotion path are suppressed for image-enrichment rescues. Metric-altering fixes (fence-strip, HR-strip) change `flat_char_count` without corresponding node-count changes, causing the verdict judge to re-evaluate documents and flip previously-stable verdicts.

#### History

- **Chain 1, 15, 16, 17, 20, 21:** Theme recurrence across runs.
- **RFC-022/024/025/026/033:** Five consecutive RFCs re-broke the same 0.17/0.30 boundary.
- **Run 8→Run 9:** GHV-TKV-Tarif identical tree flipping PASS→MARGINAL purely from threshold retune.
- **RFC-025:** Three tests expecting MARGINAL returned PASS after hysteresis.
- **Run 12→Run 13:** Reitlehrer dropped 32.2% chars from fence-strip.
- **Document 54e92c0a:** PASSed despite Article 9 leave clauses reordered after Article 13.
- **Document مرسوم 13/2022:** Earned PASS via `image_enrichment_promoted` with only 2 blocks/38 chars.
- **Cabinet Decision 106/2022:** Stored PASS despite 40% Latin-mojibake garbling detected.

#### Code Evidence

**`apply_promotions` (verdict.py:404-571):**
- D1 hard-fail gate at line ~519: `sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio`; only `_ie` (image enrichment) is allowed as exception.
- D2 pipeline at line ~533: `_try_image_enrichment > _try_structural_pass > _try_ocr_promotion > _try_flat_promotion > _try_content_class_promotion > _try_small_doc_promotion`; first match wins.
- `_apply_clamp` (line ~465): scopes `source_selection` bypass exclusively to `_is_image_enrichment=True`.
- Content-volume floor at line ~454 (`th.min_marginal_chars`): short-circuits to FAIL.
- `_try_image_enrichment` (verdict.py:226-268): runs a second `detect_garble` on `_dedupe_chart_text_lines(sig.primary_text)`, independently from `validate_tree`'s gate-table garble check.
- VG-7 fix: computes `_ie` once and shares it between D1 and D2 (line ~513).

#### Key Files

- `src/pageindex_mcp/helpers/verdict.py`
- `src/pageindex_mcp/helpers/gates.py`
- `src/pageindex_mcp/helpers/tree_validation.py`
- `src/pageindex_mcp/config.py`

---

### Zone 2: Detection-Remediation Dispatch Gap

**Severity:** Critical | **Bug count:** 4

#### Mechanism

Garble and structural defect detection fires correctly at the `validate_tree` gate stage, but the OCR/VLM recovery dispatch fails to connect to the right remediation because:

- **(a)** The GATE_TABLE severity ordering lets non-garble defects suppress co-firing garble defects as the primary reason, which recovery dispatch branches on.
- **(b)** `validate_tree`'s early-exit on `node_count<3` / `depth<2` runs before garble checks complete, so numeric-junk PDFs receive `reason='node_count<3'` and never trigger OCR recovery.
- **(c)** The recovery dispatch consumes a narrower reason set than the garble detection subsystem can emit.
- **(d)** Terminal-raise routing and flat-routing whitelist ordering can pre-empt documents from reaching gates that would correctly detect and remediate their defect.

The system has a two-phase architecture: (1) detection via exhaustive GATE_TABLE evaluation in `validate_tree`, (2) recovery via `GateSpec.recovery_eligible + recovery_fns` dispatch. The gap arises because the D4 garble-priority override (validate_tree line ~416: garble defects must win as primary when co-firing) was added as a patch AFTER the severity ordering was established, and it only covers the `GARBLING`/`NODE_GARBLING` pair. Any NEW defect type added to GATE_TABLE with lower severity than garble gates could shadow them before D4 fires. Recovery dispatch on the `_eligible_garble` / `_eligible_low_content` predicates checks `state.first_defect` or `state.ok`, which depend on the primary defect chosen — so a detection-correct but dispatch-wrong primary selection silently disables the correct recovery path.

#### History

- **Chains 3, 12, 18, 19:** Theme recurrence.
- **GATE_TABLE priority tiebreak:** NODE_COUNT_LOW (severity=1) suppresses NODE_GARBLING (severity=3) as primary.
- **Ward 597:** Persisted with garbling(ratio=1.00) and 81 garbled nodes but PASS because numeric-junk early-exit prevented garble detection and OCR escalation.
- **Ward 597 recovery:** Only reached MARGINAL by Run 16 despite VLM availability.
- **RFC-036 D3 rtl_reversal routing:** Document terminates in terminal-raise list BEFORE reaching flat-path garble gate.

#### Code Evidence

**GATES list (gates.py:359-446):**
- GARBLING severity=0
- NODE_COUNT_LOW severity=1
- DEPTH_LOW severity=2
- NODE_GARBLING severity=3

**`validate_tree` D4 override (tree_validation.py:~416):**
```python
if primary_defect not in _garble_defects:
  for d, detail in fired:
    if d in _garble_defects:
      primary_defect = d
      break
```
Only `GARBLING`/`NODE_GARBLING` pair covered.

**`_eligible_image_dominant` (gates.py:314-327):**
Zone-1 fix checks `all_defects` not just `first_defect`, so `DEPTH_LOW` as secondary still triggers recovery.

**`_gate_node_garbling` (gates.py:72-96):**
Fires when per-node garble ratio exceeds `garble_node_ratio_threshold`.

**`detect_garble` trace_path:**
11 direct callers across garble.py, pictures.py, tree_validation.py, verdict.py, recovery.py, images.py, indexer.py — each passing different text derivation and ScriptContext.

#### Key Files

- `src/pageindex_mcp/helpers/gates.py`
- `src/pageindex_mcp/helpers/tree_validation.py`
- `src/pageindex_mcp/helpers/garble.py`
- `src/pageindex_mcp/client/recovery.py`

---

### Zone 3: Converter Chain / Remote Service Boundary Drift

**Severity:** High | **Bug count:** 4

#### Mechanism

The converter chain walker (`_convert_to_tree` in indexer.py) classifies failures as transient or structural and applies a `ConverterFailurePolicy` (`RETRY`/`BLOCK_AGPL`/`GATE_AGPL_STRUCTURAL`/`WALK`/`REJECT`). `WALK` unconditionally advances to the next converter including AGPL-licensed ones for structural failures. The remote Docling microservice is independently versioned with no skew enforcement, so local fixes to normalize.py or garble.py have zero effect on documents routed through the remote converter. Fixes are implemented locally but never committed to git or deployed to the remote service. Timeout multipliers are scoped to only one PDF classification (scanned) but not another (image_based) that triggers the same OCR pipeline.

The converter chain is an ordered sequence of converters with fallback behavior on failure. The chain walker's WALK policy advances to the next converter regardless of licensing when the failure is classified as structural, which creates a licensing compliance gap (CLAUDE.md Hard Rule 4). The remote Docling microservice runs an independently-versioned, versionless container image with no API-level version assertion or contract skew enforcement. This means any local code fix (bidi heading guards, garble normalization, presentation-forms handling) only takes effect for locally-converted documents — remotely-routed documents see whatever code the remote container runs. The asymmetry means that a fix verified in local testing may have zero effect in production when the document happens to route through the remote service.

#### History

- **Chains 2, 10, 11, 13:** Theme recurrence.
- **RFC-033 D2 Part A:** `_heading_is_logical_order` guard was written but never committed to git (0 occurrences in src/ per search_code).
- **indexer.py:570-572:** Documents 'remote path does NOT forward expected_script to external Docling microservice'.
- **RFC-032 D3:** Timeout multiplier scoped only to scanned, excluding image_based — flagged as latent timeout bug.
- **RFC-036 D2:** Density-preserve fix present in working tree but never isolated into own commit.

#### Code Evidence

**`ConverterFailurePolicy` (pipeline.py:63-103):**
Enum with `RETRY`, `BLOCK_AGPL`, `GATE_AGPL_STRUCTURAL`, `WALK`, `REJECT`.
- `GATE_AGPL_STRUCTURAL` docstring: 'A structural failure would walk into an AGPL-licensed converter. Previously this was an unnamed fall-through into WALK that only emitted a warning log, so an AGPL fallback taken for structural reasons was neither gated nor counted.'
- This branch is now explicit and metricked via `AGPL_FALLBACK_TOTAL{reason='structural_walk'}` and operator-gateable via `AGPL_STRUCTURAL_FALLBACK_ENABLED` (default true).

**`PipelineConfig` (config.py):**
- `remote_version_enforce` flag at line 420
- `allow_agpl_fallback` at line 380
- `agpl_structural_fallback_enabled` at line 416

#### Key Files

- `src/pageindex_mcp/client/indexer.py`
- `src/pageindex_mcp/converters/pipeline.py`
- `src/pageindex_mcp/converters/normalize.py`
- `src/pageindex_mcp/config.py`

---

### Zone 4: OCR Pipeline Conflation

**Severity:** High | **Bug count:** 3

#### Mechanism

The OCR subsystem has three independently-gated escalation triggers (garble, per-picture, low-content) plus a fourth structural-failure/image-dominant path, but these are not fully independent in practice. Per-picture OCR fires unconditionally during PDF-to-markdown conversion (including during `force_full_page_ocr` escalation calls), so two competing Tesseract passes run on the same region. Text recovered by per-picture OCR moves from prose blocks into image-block `ocr_text` fields, which `content_class` computation and `flat_char_count` metrics are blind to. Standalone image files bypass the PDF picture-enrichment splice path entirely, losing chart/picture OCR content. The P0b 60% page-coverage filter only blocks oversized regions, allowing sub-threshold charts to be re-OCR'd when Docling's own text layer already extracted the labels cleanly.

The conflation arises from overloading a single conversion pipeline (`pdf_to_markdown_docling`) with both page-level OCR recovery and per-picture region OCR. `_recover_picture_results` (pictures.py:1036-1123) is gated on `pipeline_config.ocr_escalation_per_picture` and the presence of `<!-- image -->` markers, but it fires as part of the standard conversion flow AND again during `force_full_page_ocr` re-extraction. The Zone-2 re-entry guard (`force_full_page_ocr_applied` parameter) was added to short-circuit the second invocation, but the root structural issue — a single conversion function handling two conceptually different OCR strategies — remains. Content-type boundary gaps recur at every new ingestion route: standalone images call `image_to_markdown()` directly, bypassing `splice_figure_markers`/`_enrich_image_blocks`; the fix (P0a) duplicated PictureResults N times, creating storage waste.

#### History

- **Chains 4, 5, 9:** Theme recurrence.
- **feat/image-block-picture-ocr branch:** Per-picture OCR fires unconditionally including during `force_full_page_ocr` calls.
- **P0b `_PICTURE_PAGE_COVERAGE_THRESHOLD` (default 0.6):** Only filters regions above 60%; charts at 15% page area still re-OCR'd.
- **D1 text-layer probe fix:** Implemented but left UNCOMMITTED.
- **Standalone image f057fafe:** Pie-chart jpg blocks show literal '<!-- image -->' with wedge/label text lost.
- **P0a fix (commit cad3f63):** Duplicated N PictureResults for standalone images.

#### Code Evidence

**`_recover_picture_results` (pictures.py:1036-1123):**
Gated on `decide_ocr_strategy(ocr_escalation_enabled=pipeline_config.ocr_escalation_per_picture, has_image_markers=_IMAGE_MARKER in md, full_page_already_applied=force_full_page_ocr_applied)`.

**Zone-2 re-entry guard:**
'When `force_full_page_ocr_applied=True`, a full-page OCR retry has already re-extracted all page content including picture regions. Per-picture OCR would duplicate that work, so we short-circuit to []'.

**`_text_layer_has_content` (pictures.py:240-275):**
Calls `detect_garble` on extracted text layer content as OCR-skip probe.

**`PipelineConfig` (config.py:382-384):**
Three independent OCR triggers:
- `ocr_escalation_garble`
- `ocr_escalation_per_picture`
- `ocr_escalation_low_content`

#### Key Files

- `src/pageindex_mcp/converters/pictures.py`
- `src/pageindex_mcp/client/images.py`
- `src/pageindex_mcp/client/indexer.py`
- `src/pageindex_mcp/config.py`

---

### Zone 5: Content Measurement Blind Spot

**Severity:** High | **Bug count:** 3

#### Mechanism

Multiple code paths across both the ingestion pipeline and the corpus-audit scoring harness use `block.get('text', '')` to measure content volume, which returns 0 chars for `role='table'` blocks by design — table cell content lives in `headers`/`rows`/`row_records` instead. This creates a self-reinforcing false measurement cycle: the pipeline under-measures content → the audit tooling (sharing the same blind spot) confirms the low number → the operator trusts the audit → RFCs are designed to fix problems that were partly created by the measurement bug itself. The correct helpers exist (`_flat_block_primary_text` in flat.py:174-196 folds `row_records`; `_flat_search_text` in flat.py:199-221 handles table/image/prose blocks correctly) but are not used uniformly.

The architectural root cause is a schema design decision: flat-doc blocks with `role='table'` carry their content in `row_records` (a list of pipe-delimited row strings) and `headers` (a list of column names), with NO 'text' key. This is by-design (FLAT-05-C1) for structured data fidelity. But the char-count measurement path (used for `flat_char_count`, verdict-promotion synthetic-structure builder, and audit scoring) must call a role-aware helper to extract text from the correct key per role. When any new measurement site is added using the naive `block.get('text','')` pattern, it silently under-counts by the entire table content — which can be the majority of a document's chars (e.g. GHV-TKV-Tarif: 13,022 raw chars measured as 375). The corpus audit harness had a separate but compounding process-integrity bug where the score-stage never invoked the code path that consumed persisted MinIO metas, defaulting all documents to ERROR with null node_count/chars, producing a fabricated Run 9 corpus report.

#### History

- **Chains 6, 14, 22:** Theme recurrence.
- **GHV-TKV-Tarif:** 13,022 raw chars → 375 measured chars from 3 tables with no text key.
- **Unfallversicherung:** Meta counter showing 7,471-7,408 char variance, benefit-comparison table 75% empty cells in persisted form.
- **Run 9 corpus scoring:** 'score-stage entry point never reads MinIO results; arq job handler hands off incorrectly; all 24 documents defaulted to verdict=ERROR despite real PASS/MARGINAL data in MinIO meta.'
- **Run 16 reconciliation:** 'stored PASS verdicts persisting uncorrected on documents this session judged FAIL'.
- **Fabricated Run 9 report:** Partly drove RFC-015 design decisions.

#### Code Evidence

**`_flat_block_primary_text` (flat.py:174-196):**
Correctly falls back to `row_records` for table blocks, then to `headers` for header-only tables (Zone-9 fix).

**`_flat_search_text` (flat.py:199-221):**
Correctly handles:
- `role=table` (row_records)
- `role=image` (ocr_text + description)
- default (text key)

**Naive measurement pattern:**
Both audit tooling and historically in `content_signals` computation use `block.get('text','')`.

**Correct usage:**
client.py lines ~1158-1176 already use `_flat_block_primary_text` for verdict computation (RFC-022 B3 fix), but the audit harness was not updated to match.

#### Key Files

- `src/pageindex_mcp/helpers/flat.py`
- `src/pageindex_mcp/client/indexer.py`
- `src/pageindex_mcp/storage/verdict.py`

---

### Zone 6: Verdict Persistence Asymmetry

**Severity:** Medium | **Bug count:** 2

#### Mechanism

Verdict data is persisted through a two-tier cascade with an explicit process boundary: `save_doc_meta` (MinIO sidecar, eventual consistency, no write-visibility barrier) runs in the isolated converters_cli child subprocess, then the worker parent performs the authoritative Postgres write via `_upsert_registry_row` → `upsert_doc` (RFC-037 D5 max-priority-wins arbiter). This creates three writers with asymmetric consistency guarantees (`save_doc` with read-after-write barrier, `save_doc_meta` with barrier-less 'eventual' write, `registry_mirror` with best-effort sidecar backfill). Erasure operations must manually enumerate every storage prefix to purge via `_ERASURE_MANIFEST`, which drifts out of sync when new ingestion routes add prefixes.

The three-writer asymmetry generates bugs because:

1. The sidecar can transiently disagree with Postgres, and any code path that reads verdict from the sidecar without accounting for this window risks staleness.
2. `read_registry_fields` (verdict.py:252-322) implements a two-source fallback (artifact body checked first for legacy docs, sidecar fallback for Zone-5+ docs) that is correct but fragile — any new caller that reads verdict directly from MinIO without going through this function sees stale or missing data.
3. The child→parent process boundary means `save_doc_meta` can succeed (MinIO sidecar written) but `_upsert_registry_row` can fail (Postgres not written), leaving the stores permanently divergent until `reconcile_registry_drift` runs.
4. The erasure manifest (`_ERASURE_MANIFEST`) is a manually-maintained tuple of storage prefixes, so any new ingestion route that adds a prefix (e.g. preloaded/) silently creates an erasure coverage gap.

The deprecated `write_verdict` wrapper (verdict.py:201-232) adds a fourth entry point with zero live production callers — only legacy scripts — that could diverge if reactivated.

#### History

- **Chains 7, 8:** Theme recurrence.
- **save_doc_meta:** Removed `_confirm_write_visible` barrier that `save_doc` retains.
- **reconcile.py:** Needed load-bearing ordering fix: drain verdict-retry queue BEFORE MinIO etag diff scan.
- **New ingestion routes:** (preloaded/ prefix) never added to erasure manifest (ISS-41).
- **Registry-delete:** Historically fire-and-forget, logs success on silent failure (ISS-40).
- **write_verdict:** Zero live production callers per trace_path.

#### Code Evidence

**`save_doc_meta` (verdict.py:78-198):**
- Line ~186: stamps `consistency_model='eventual'`
- Line ~192: 'write-visibility barrier removed. Postgres is the sole verdict authority; the sidecar is archival-only.'
- Contrast with `save_doc` in documents.py which retains the barrier.

**`_upsert_registry_row` (registry_mirror.py:56-200):**
Runs in worker parent process, calls `upsert_doc` (Postgres CAS), then best-effort backfills sidecar via `save_doc_meta` with `consistency_regime='postgres-authoritative'`.

**`write_verdict` (verdict.py:201-232):**
Docstring: 'Deprecated (Zone-5): thin wrapper that delegates to save_doc_meta... Retained only for legacy callers (promotion_sweep.run_sweep, preprocess_client.recompute_verdicts)'.

**`read_registry_fields` (verdict.py:252-322):**
Artifact body first, sidecar fallback, with Zone-5 NOTE explaining new artifacts lack verdict fields in body.

#### Key Files

- `src/pageindex_mcp/storage/verdict.py`
- `src/pageindex_mcp/storage/documents.py`
- `src/pageindex_mcp/worker/registry_mirror.py`
- `src/pageindex_mcp/storage/queries.py`

---

## Cross-Cutting Themes

1. **Verdict-gate threshold ratchet:** Widening a boundary (e.g. `PASS_MAX_LEAF_RATIO` 0.17→0.30) to fix one class of false-FAIL always unmasks a new class of false-PASS, and every threshold edit invalidates test fixtures calibrated to the old value — this same fight recurred across RFC-022, 024, 025, 026, 033.

2. **Detection is wired to a narrower reason-set than remediation consumes:** Garble/garbling detection fires correctly at the verdict stage, but OCR recovery escalation and the GATE_TABLE priority tiebreak only look at a subset of reasons, so a correctly-detected garbled document can still fail to reach its recovery hook.

3. **Shared kill-switches conflate independent concerns:** A single `_OCR_ESCALATION` env var/flag gates both page-level OCR escalation and per-picture image enrichment, so a fix or toggle aimed at one silently changes the other's behavior.

4. **Fixes land locally but never reach production:** The RFC-033 bidi heading guard was never committed to git, and the remote Docling microservice is versionless and independently deployed, so local patches to normalize.py/garble.py have zero effect on remotely-routed documents.

5. **Audit and diagnostic tooling inherits the exact same structural blind spot as the pipeline it measures:** Most concretely, `block.get('text','')` returning 0 chars for `role='table'` blocks in both the verdict-promotion code and the corpus-audit scoring harness — producing self-reinforcing false confidence (or false failure) cycles and at least one fabricated corpus report (Run 9).

6. **Duplicated/divergent implementations drift independently:** `decide_ocr_mode` vs `decide_ocr_strategy` silently diverge in parameter forwarding; local vs remote bidi normalization run different code versions; `_tree_is_garbled` vs `_flat_text_is_garbled` repeat the same digit-ratio floor bug.

7. **Process safeguards substitute for root-cause fixes:** Mandatory pre-publish MinIO re-verification (RFC-025 D4) prevents publishing wrong numbers but doesn't fix the scoring-harness bug that produces them in the first place; commit-isolation gaps leave verified working-tree fixes marked as incomplete tasks.

8. **Reconciliation audits sometimes contradict each other's ground truth:** An audit finding and its matching RFC decision can each be individually correct about different, adjacent code paths (e.g. Ward 597 'blind spot' claim vs the actual terminal-raise ordering) — apparent contradictions often resolve to a routing/ordering misunderstanding rather than a real conflict.

9. **Ingestion-route coverage gaps recur at every new content-type boundary:** Standalone image files bypass the PDF picture-enrichment splice path entirely; new storage prefixes (e.g. preloaded/) are omitted from the manually-maintained erasure manifest; image_based PDFs are omitted from the OCR timeout multiplier scoped only to scanned PDFs.

10. **Asymmetric consistency guarantees across dual/triple writers:** (`save_doc`'s MinIO barrier vs `save_doc_meta`'s barrier-less 'eventual' write vs `registry_mirror`'s best-effort sidecar) create races that erasure and reconciliation logic must paper over with load-bearing operation ordering rather than a single transactional model.

11. **Detection without remediation:** Gates fire correctly but don't trigger recovery mechanisms; VLM fallback is workaround for deeper early-exit/escalation logic failure.

12. **Metric-driven verdict drift:** Threshold tweaks (fence stripping, `PASS_MAX_LEAF_RATIO` widening) cause verdict flips without content changes; judges reweight on metric variance, not real extraction quality.

13. **Promotion paths bypass structural checks:** Image enrichment and recovery promotion can reach PASS despite zero/garbled content; gate becomes promotion-rule-agnostic.

14. **Measurement/storage bifurcation:** Metrics computed during extraction diverge from persisted content (meta shows 7,471 chars but blocks only 492); harness must verify against live MinIO, not triage.json.

15. **Heuristic saturation and blindspots:** Single metrics insufficient; Arabic-only PUA misses Latin mojibake, markdown dilutes digit-ratio, early-exit prevents garble detection, no document-order validation.

16. **Verdict-gate softening masks recovery gaps:** RFC-025 threshold widening eased FAIL→MARGINAL without triggering content recovery; audit cannot trust verdict labels; stored PASS persists on documents fresh reingestion judges FAIL.

---

## Simplification Proposals

### Verdict Promotion / Threshold Ratchet

1. CORE SIMPLIFICATION: Collapse the six ad-hoc `_try_*` promotion predicates plus the D1 hard-fail exception into a single declarative table of (predicate, clamp-scope, priority) entries evaluated by one generic loop, and make the `source_selection` bypass a per-entry flag on that table instead of a hardcoded `_is_image_enrichment` special case threaded through `_apply_clamp`. This removes the structural asymmetry (image_enrichment is 'special', everything else is 'normal') that caused the bidi/depth-cap bypass bug, and makes every future promotion path opt into or out of the bypass explicitly at table-definition time rather than by matching a magic string.

2. CONCRETE STEPS: verdict.py (~404-571, ~167 lines) — replace _try_cat_a/_try_cat_b/_try_cat_c/_try_small_doc/_try_structural_pass/_try_image_enrichment plus the RFC-alias block (~397-402) with a PROMOTION_TABLE list of (name, predicate_fn, clamp_bypass_eligible) tuples; delete the alias shim (-6 lines). Replace the D2 if/elif chain (~15 branches, ~40 lines) with a single `for rule in PROMOTION_TABLE` loop (~10 lines) — net verdict.py delta roughly -60 lines. _apply_clamp (~465-490): drop the _is_image_enrichment kwarg, source clamp-bypass from the matched rule tuple. D1 exception (~519-529): keep computing _ie once (VG-7) but source its bypass flag from the same table so D1/D2 can never diverge. gates.py/tree_validation.py/config.py: unchanged — this only touches dispatch mechanism, not calibration.

3. BUG CLASSES PREVENTED: image_enrichment-style clamp bypass leaking to a new promotion path without updating _apply_clamp's conditional; D1/D2 _ie divergence recurring (VG-7 class); implicit if/elif priority ordering hiding what wins when a new path is added.

4. MIGRATION RISK: Low-medium. promotion_paths_matched (VG-6) telemetry must stay byte-identical — a table-driven loop in current source order reproduces it. Sequence: (1) extract table with verbatim predicate bodies, run full verdict fixture suite for exact reason-string equality; (2) only afterward, in a separate commit, consider any threshold re-tuning. Never combine the refactor with a threshold change in one commit.

5. EFFORT: ~0.5-1 day.

### Detection-Remediation Dispatch Gap

1. CORE SIMPLIFICATION: Delete the _garble_defects-scoped D4 override in validate_tree (tree_validation.py ~410-419) and instead pick primary_defect by the severity field GateSpec already carries (severity=0..3 in gates.py) via `min(fired, key=lambda t: t[0].severity)`, rather than 'table order, patched for two specific defect types.' This generalizes D4's fix to every gate pair instead of only GARBLING/NODE_GARBLING.

2. CONCRETE STEPS: gates.py — confirm every GateSpec in GATE_TABLE (~359-446) carries a severity value; fill in any missing ones (~15-20 lines). tree_validation.py (~416-424, ~9 lines replaced by ~2): delete the D4 special-case block (`_garble_defects = {...}; if primary_defect not in _garble_defects: for d, detail in fired: ...`); replace `primary_defect, primary_detail = fired[0]` with the severity-min call. gates.py _eligible_garble/_eligible_low_content/_eligible_image_dominant (~314-327 and neighbors): switch each from `state.first_defect` to `state.all_defects` membership (the Zone-1 fix already did this for _eligible_image_dominant; apply uniformly, ~4 predicates x ~2 lines). Do not attempt to consolidate the 11 detect_garble call sites in this pass — flag as a separate follow-up.

3. BUG CLASSES PREVENTED: a future gate added at a severity between the current bands silently shadowing a garble defect as primary (exactly the zone's described risk), since severity — not table position — now decides; recovery suppression when a defect is eligible but non-primary, for any gate beyond the two Zone-1 already patched.

4. MIGRATION RISK: Low. min() over the current severity values reproduces D4's exact behavior for the pairs it covers today, and additionally fixes others — a pure generalization. Sequence: (1) audit/assign severity, unit-test that severity-min matches current table-order output on all fixtures; (2) swap fired[0] for the min() call and delete the special case; (3) widen _eligible_* predicates to all_defects one at a time behind existing recovery-path tests, since that step is a real behavior change (more recoveries firing) and needs per-predicate review.

5. EFFORT: ~1 day.

### Converter Chain / Remote Service Boundary Drift

1. CORE SIMPLIFICATION: Don't try to eliminate the local/remote asymmetry itself (that needs a versioned container image, an infra project not a code simplification) — instead make it impossible to ship silently, by turning remote_version_enforce from an inert config flag into an actual enforced check on every remote Docling call, and collapse ConverterFailurePolicy's decision logic so GATE_AGPL_STRUCTURAL vs WALK is derived from two booleans (is_structural, next_is_agpl) via one lookup instead of scattered inline checks duplicating what the enum already encodes.

2. CONCRETE STEPS: config.py (~420) — remote_version_enforce becomes enforced in pipeline.py's remote-call wrapper: compare the remote container's reported version against a pinned expected_remote_docling_version; mismatch logs+metrics (or hard-fails once soaked) instead of silently proceeding (~30-40 new lines, this is new enforcement, not a deletion). pipeline.py ConverterFailurePolicy (~63-103): keep all 5 enum values (well-documented, load-bearing) but audit _convert_to_tree's inline call site for any branch logic duplicating what the enum's decision function already encodes, and delete the duplicate (~15-20 removable lines if found). normalize.py/indexer.py: confirm local and remote paths both run the same post-processing normalization (bidi guards, garble normalization) unconditionally, so at least normalization-layer fixes apply uniformly even though extraction-layer fixes inside the AGPL converter itself cannot.

3. BUG CLASSES PREVENTED: a remote Docling container silently drifting version with no operator signal (root cause of 'local fix has zero effect in prod'); any remaining implicit AGPL fallthrough not gated/metricked (extends the existing GATE_AGPL_STRUCTURAL discipline to be the only path).

4. MIGRATION RISK: Medium — version enforcement can break ingestion if the pinned version is stale; must ship observe-only (metric, no reject) for one deploy cycle first. Sequence: (1) add version comparison as metric-only; (2) soak ~1 week confirming the remote container reports a stable version; (3) flip to enforcing with an env-var rollback documented.

5. EFFORT: ~1-1.5 days plus a week of soak before enabling hard enforcement.

### OCR Pipeline Conflation

1. CORE SIMPLIFICATION: Split the conflated function's two OCR strategies (page-level full-page OCR vs. per-picture region OCR) into two separately-named functions sharing only the low-level OCR call, and move the double-invocation guard from a threaded parameter (force_full_page_ocr_applied) into a single boolean check at the caller. The guard exists only because one function currently does two jobs; splitting removes the need for the guard rather than hardening it.

2. CONCRETE STEPS: pictures.py _recover_picture_results (~1036-1123, ~87 lines) — extract the per-picture-region OCR body into a standalone _ocr_picture_regions(md, pipeline_config) with no force_full_page_ocr_applied parameter. Caller (pictures.py/indexer.py conversion entry point): make the flow strictly sequential — `if full_page_ocr_needed: md = full_page_ocr(...)` then `if per_picture_ocr_needed and not full_page_ocr_ran: md = _ocr_picture_regions(...)` — the short-circuit becomes one caller-side boolean instead of a parameter threaded through multiple call levels (net ~10 fewer lines, and the short-circuit is now visible at the call site). _text_layer_has_content (~240-275): leave unchanged — it's a decision-input probe, not part of the conflated dual-strategy body, out of scope here. images.py image_to_markdown(): route standalone-image ingestion through the shared splice_figure_markers/_enrich_image_blocks path instead of the duplicated PictureResults construction from the P0a fix (~20-30 lines removable once equivalence is confirmed).

3. BUG CLASSES PREVENTED: double OCR (duplicate cost/work) whenever a new ingestion route is added and forgets to thread force_full_page_ocr_applied correctly — after the split there's no parameter to forget, the sequential if/elif at the caller makes double-invocation structurally impossible.

4. MIGRATION RISK: Low-medium. The extracted functions are behavior-identical branches of the current conflated function so per-branch unit tests should be unaffected; risk is in caller wiring reaching the same short-circuit the guard used to provide. Sequence: (1) extract _ocr_picture_regions verbatim, tests green; (2) move the short-circuit to the caller, delete the parameter, confirm against the existing Zone-2 re-entry regression test; (3) tackle image_to_markdown dedup as a separate smaller follow-up (storage-cost only, not correctness-critical).

5. EFFORT: ~1 day for the OCR split; ~0.5 day follow-up for the image_to_markdown dedup.

### Content Measurement Blind Spot

1. CORE SIMPLIFICATION: The correct role-aware helpers (_flat_block_primary_text, _flat_search_text in flat.py) already exist and are already correct per the zone's own evidence — the bug is purely about discoverability/enforcement, not missing logic. So the simplification is a CI/lint guard that fails the build on any naive `block.get("text"` pattern outside flat.py, rather than a code restructuring.

2. CONCRETE STEPS: flat.py (~174-221) — no logic change; optionally add a non-underscore-prefixed export alias (e.g. flat_block_text = _flat_block_primary_text) since these are now the canonical cross-module API rather than private helpers (~2 lines). Add a new CI check (grep-based test, ~15-20 lines, e.g. tests/test_no_naive_block_text.py) asserting no file under src/ or audit/ calls `.get("text"` on a dict named block outside flat.py — this catches the exact class of bug described (a new measurement site written against the naive pattern) at review time instead of via a fabricated corpus report. client/indexer.py, storage/verdict.py: already fixed per RFC-022 B3 — the new check just guards against regression, no code change needed. Audit harness (Run 9 corpus report generator): the score-stage-never-invoking-the-meta-consuming-path bug is a separate, compounding defect (control flow, not measurement helper) — fix independently: verify the score stage actually calls the code path that reads persisted MinIO metas before defaulting to ERROR/null. Do not fold this fix into the same commit as the CI guard.

3. BUG CLASSES PREVENTED: any future audit/reporting/scoring tool re-introducing the table-content undercount by bypassing the role-aware helper, caught at CI time instead of discovered post-hoc in a fabricated corpus report.

4. MIGRATION RISK: Very low for the CI guard (purely additive; known-correct call sites already comply, so it cannot break anything currently passing). The audit-harness score-stage fix carries the real risk since it changes corpus report output — sequence it as its own PR, verify against a known document (e.g. GHV-TKV-Tarif) with an expected char count before trusting the next corpus report.

5. EFFORT: ~0.5 day for the CI guard; ~0.5-1 day separately for the audit-harness score-stage investigation and fix.
