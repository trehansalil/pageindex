# Architecture Defect Zones Audit — 2026-08-12 POST

**Date:** 2026-08-12
**Run:** POST
**Audit Scope:** Critical & High severity zones identified across verdict-gating, garble detection, OCR recovery, consistency modeling, and converter pipeline subsystems

---

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|------|----------|-----------|-----------|
| 1 | Verdict-Gate Cascade | CRITICAL | 11 | verdict.py, tree_validation.py, gates.py, config.py |
| 2 | Garble Detection Kernel | CRITICAL | 7 | garble.py, pictures.py, tree_validation.py, config.py |
| 3 | OCR Recovery Cascade | HIGH | 5 | picture_plane.py, ocr_langs.py, gates.py, pictures.py |
| 4 | Measurement and Audit Self-Reinforcing Blind Spot | HIGH | 4 | verdict.py, queries.py |
| 5 | Dual-Write Consistency Model | HIGH | 3 | verdict.py, documents.py, registry_mirror.py, queries.py |
| 6 | Converter Pipeline and Deployment Gap | HIGH | 3 | pipeline.py, indexer.py, config.py |
| 7 | Erasure Cascade (Manually-Maintained Manifest) | MEDIUM | 2 | documents.py, queries.py |

**Total Attributed Bugs: 35**

---

## Zone Details

### Zone 1: Verdict-Gate Cascade

**Severity:** CRITICAL | **Bug count:** 11

The verdict computation pipeline (validate_tree → evaluate_gates → apply_promotions → classify_verdict) uses a first-match-wins ordered promotion chain with numeric thresholds stored as unversioned module constants. Any threshold change invalidates prior calibrations and test fixtures, producing a threshold-tuning ratchet: five consecutive RFCs (022, 024, 025, 026, 033) each independently fixed and re-broke the same verdict boundary.

#### Mechanism

First-match-wins promotion pipeline where source-code order IS the specification. Widening a threshold (PASS_MAX_LEAF_RATIO 0.17→0.30) reveals previously-masked defects at the new edge; tightening reveals a different set and regresses previously-passing documents.

The source_selection=True path in image-enrichment promotion bypasses _apply_clamp entirely (verdict.py:451-453), letting documents with as few as 38 characters PASS.

The hysteresis band (RFC-025 D0) that widens the leaf-ratio threshold when prior_verdict==PASS combines with four interconnected bugs (_script_from_filename returning None, Latin-gibberish heuristic needing expected_script, classify_verdict hardcoding None, and threshold widening) to flip previously-FAIL garbled documents to PASS.

Gate racing: when rtl_reversal fires in the terminal-raise list (client.py:1992) BEFORE the flat-path garble gate, the garble gate never executes, making audit conclusions about that gate's coverage unreliable.

#### History

- **Chain 7:** GATE_TABLE severity ordering lets node_count_low mask garbling reason, preventing OCR-recovery wiring.
- **Chain 12:** PASS_MAX_LEAF_RATIO widened 0.17→0.30 allowed 81-garbled-node docs to PASS.
- **Chain 13:** hysteresis reclassified zero-content failures FAIL→MARGINAL (HR5).
- **Chain 14:** source_selection=True bypasses _apply_clamp; ZONE_DELTA shows 3 additional bugs from WAVE3 attempt (5→8 bug regression).
- **Chain 15:** hardening produced 12 corpus regressions.
- **Chain 16:** five consecutive RFCs each fixed and re-broke the same boundary; every threshold change invalidated test fixtures.
- **Chain 24:** gate racing — rtl_reversal terminal-raise pre-empts flat-path garble gate.
- **Chain 28:** image_enrichment_promoted path bypassed absolute character-count floor; four zero-character Arabic documents passed.
- **Chain 29:** hysteresis + four interconnected expected_script threading bugs flip FAIL→PASS on garbled German document.
- **Chain 31:** _tree_is_reordered added to validate_tree but classify_verdict wiring incomplete.
- **Chain 34:** verdict-label softening FAIL→MARGINAL across Runs 8-9 created false improvement impression.

#### Code Evidence

**apply_promotions** (verdict.py:380-501): ordered if/elif pipeline, source_selection bypass at line 451-453 within _apply_clamp — `if source_selection and _is_image_enrichment: return VerdictResult("PASS", ...)` skips _clamp_pass.

**validate_tree** (tree_validation.py:333-451): garble-type defect promotion override at lines 414-421 `if primary_defect not in _garble_defects: for d, detail in fired: if d in _garble_defects: primary_defect = d` — layered ordering dependency on top of GATE_TABLE severity.

**GATES definition** (gates.py:359-446): severity 0=GARBLING, 1=NODE_COUNT_LOW, 3=NODE_GARBLING — so NODE_COUNT_LOW fires as primary before NODE_GARBLING when both co-fire, requiring the override.

**_clamp_pass** (verdict.py:103-122): only caps on bidi_degraded and depth_inadequate — does not enforce garble or content-volume caps.

**compute_verdict** (verdict.py:504-547): thin dispatcher, evaluate_gates → apply_promotions with hard_fail short-circuit.

#### Key Files

- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/config.py

---

### Zone 2: Garble Detection Kernel

**Severity:** CRITICAL | **Bug count:** 7

detect_garble (garble.py:529-614) is a 15+-caller shared choke point for all garble decisions across converters, client recovery/indexer, helpers/gates, tree_validation, and picture recovery. A narrow fix to one prong or GarbleConfig threshold silently changes behavior for every other caller.

NFKC normalization runs BEFORE the detector and destroys Arabic presentation-form codepoints (U+FB50-FEFF) — the detector's only bidi-corruption signal — yielding a 0% true-positive rate (null-detector fallacy).

#### Mechanism

Single-surface API with 15+ direct callers: any change to GarbleConfig defaults, the RFC-025 D2 short-circuit, or any prong threshold has broad blast radius across OCR escalation, verdict gating, and picture-text recovery simultaneously.

The NFKC destruction problem is fundamental: pipeline normalization decomposes Arabic presentation-form codepoints before detect_garble sees the text, and the compensating heuristic (garble.py:585-593, inferring had_presentation_forms=True when _arc>0 and _pf==0) is a workaround that cannot fully undo the destruction.

The duplicate-implementations problem (originally _tree_is_garbled vs _flat_text_is_garbled) was consolidated into the shared kernel, but consolidation created a NEW problem: the garble_short_text_default config flag (forcing is_garbled=True for <200-char blobs with prior garble defect) became a hidden global mode switch affecting all 15+ callers.

The digit-ratio prong in garble_prongs (garble.py:399-410) is gated behind cfg.garble_digit_floor, so short numeric-junk blobs escape detection. The FLAT-03 routing path (route_and_extract_flat) entirely bypasses validate_tree, meaning digit-junk corruption passes with zero quality gate.

#### History

- **Chain 2:** NFKC normalization destroys Arabic presentation-form codepoints before _check_bidi_coherence sees the text; unicodedata.name() substring match for 'FINAL FORM'/'INITIAL FORM' returns nothing post-NFKC; measured 'zero violations' was a null-detector fallacy.
- **Chain 6:** _tree_is_garbled and _flat_text_is_garbled both gated digit-ratio behind len(blob)>500; fix landing in one was not guaranteed to land in the other; consolidation into shared kernel created hidden mode switch.
- **Chain 25:** _text_layer_has_content and _gate_node_garbling construct throwaway ScriptContext with had_presentation_forms=False when script_context is None, breaking the garble-detection contract.
- **Chain 26:** validate_tree early-exit (node_count<3, depth<2) ran BEFORE garble check, masking garbled-text corruption as structural defect.
- **Chain 27:** RFC-026 D5 reordered garble check to run before node-count/depth exits, but numeric-junk and Latin-script mojibake still escape detection.
- **Chain 33:** FLAT-03 routing path has zero text-quality gate — digit-junk doc 4f37b2e3 (86% digits) routed to flat-doc success path.
- **Chain 2 (bidi):** had_presentation_forms compensating fallback at garble.py:589 explicitly acknowledges the NFKC destruction it cannot fully undo.

#### Code Evidence

**detect_garble** (garble.py:529-614): confirmed 15+ callers via trace_path — _keep_best_wins, _garble_check_nodes, _garble_check_flat_blocks, _garble_ratio, _text_layer_has_content, _document_level_text_fallback, tree_validation.py, _try_image_enrichment, _attempt_tesseract_raster_recovery, _convert_to_tree, check_garble, and more at hop 2.

**garble_prongs** (garble.py:339-440): digit-ratio check at lines 399-403 `if len(norm) > cfg.garble_digit_floor: digits = sum(...); if (digits/len(norm)) > 0.60: prongs.add("digit_ratio")` — short blobs below the floor skip this prong entirely.

**Secondary short-text check** (lines 404-410): requires len>=50 and 90% threshold — still misses intermediate-length numeric junk.

**detect_garble compensation** (lines 585-593): NFKC compensation `elif _arc > 0 and _pf == 0 and _effective_script == "Arabic": _had_pf = True` — assumes raw document had presentation forms when zero survive post-normalization.

**_text_layer_has_content** (pictures.py:269-272): ScriptContext fallback construction — now calls _infer_pf (Zone-7 fix), closing the hardcoded-False gap.

#### Key Files

- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/config.py

---

### Zone 3: OCR Recovery Cascade

**Severity:** HIGH | **Bug count:** 5

OCR recovery is structurally decoupled from garble detection: detection fires at verdict stage, but recovery is gated on a narrower set of early-stage validation reasons. A single _OCR_ESCALATION kill-switch conflates page-level and per-picture OCR with no independent control.

The OCR coverage-skip filter suppresses wasteful OCR without a corresponding marker-removal step. Language availability (ensure_tessdata) raises for missing non-Latin scripts but falls back to Latin-only OCR when the empty-available path is hit.

#### Mechanism

Detection-to-remediation gap: GATE_TABLE evaluates all 10 gates exhaustively (gates.py:359-446), but OCR-recovery eligibility predicates (_eligible_garble, _eligible_low_content, _eligible_image_dominant) are narrower than the full gate set. When GATE_TABLE severity ordering lets node_count_low (severity=1) mask garbling (severity=0 but promotion-overridden only when co-firing), the document's reported reason may not match the recovery-eligible set, so recovery never wires up despite correct detection.

The single kill-switch problem (decide_ocr_strategy, picture_plane.py:357-430): the ocr_escalation_enabled parameter gates the PER_PICTURE branch, but disabling it also prevents page-level escalation in callers that use the same flag.

The marker-removal gap: the 60%-page-area coverage filter correctly suppresses OCR, but _recover_picture_results dense-fills an empty PictureResult(), and splice_figure_markers neutral-marker fallback preserves literal `<!-- image -->` verbatim in output.

ensure_tessdata (ocr_langs.py:92-196) now raises TessdataUnavailableError for non-Latin scripts, but the all-Latin-languages-dropped path still falls back to ['deu','eng'] regardless of what was requested.

#### History

- **Chain 5:** ensure_tessdata silent Latin fallback — Arabic OCR-escalation request silently ran Latin-only OCR, producing garbled Latin mojibake that passed every garble-gate prong (ISS-34).
- **Chain 7:** GATE_TABLE severity ordering lets node_count_low mask garbling reason; OCR-recovery gated on narrower set, so detected garble never reaches recovery hook.
- **Chain 8:** coverage filter (RFC-017/018 D0) skips OCR but never removes markers — splice_figure_markers neutral-marker fallback preserves `<!-- image -->` verbatim.
- **Chain 9:** per-picture text-layer probe (RFC-018 D1) implemented but left UNCOMMITTED — garbled small-font numerals from Tesseract spliced over clean extracted text.
- **Chain 10:** _OCR_ESCALATION conflates page-level and per-picture OCR — disabling one necessarily disables the other.
- **Chain 11:** decide_ocr_mode legacy wrapper forwarded document_type/ocr_langs only at lines 460-468 for its single caller, not for all call sites using decide_ocr_strategy directly.

#### Code Evidence

**decide_ocr_strategy** (picture_plane.py:357-430): ordered if-chain where `full_page_already_applied` re-entry guard (line 391) runs FIRST, then UNIFIED_OCR_PLAN_ENABLED image-document short-circuit (line 405), then force_full_page (line 413), then ocr_escalation_enabled+has_image_markers (line 418). Single ocr_escalation_enabled parameter gates PER_PICTURE mode with no separate page-level control.

**GATES definition** (gates.py:359-446): GARBLING at severity=0 with recovery_eligible=_eligible_garble and recovery_fns=('_recover_garble_ocr','_recover_vlm_fallback'); NODE_COUNT_LOW at severity=1 with recovery_eligible=_eligible_low_content.

**ensure_tessdata** (ocr_langs.py:92-196): now raises TessdataUnavailableError for non-Latin missing scripts (lines 128-131, 183-188) but empty-available Latin-only path at lines 189-195 still returns ['deu','eng'] fallback.

#### Key Files

- src/pageindex_mcp/picture_plane.py
- src/pageindex_mcp/converters/ocr_langs.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/converters/pictures.py

---

### Zone 4: Measurement and Audit Self-Reinforcing Blind Spot

**Severity:** HIGH | **Bug count:** 4

The corpus audit's diagnostic tooling inherits the pipeline's own structural blind spots, creating a self-reinforcing cycle where pipeline bugs and audit-tool bugs agree with each other. The char-count scoring uses block.get('text','') in both the verdict-promotion code and the corpus audit diagnostic, scoring 0 for role='table' blocks (where content lives in rows/cells).

The scoring harness's score-stage never invoked read_registry_fields, defaulting all documents to ERROR. RFC-025 D4's pre-publish verification is a process workaround, not a root-cause fix. A fabricated corpus report was published to Confluence and used as baseline for remediation planning.

#### Mechanism

Self-reinforcing measurement cycle: the pipeline measures content via block.get('text',''), which returns 0 for table blocks. The audit tool built to independently verify the pipeline uses the IDENTICAL block.get('text','') pattern.

A table-heavy document scores 0 chars in BOTH systems simultaneously, making it impossible to tell from the audit alone whether a low score is a real pipeline defect or a shared measurement bug. The scoring harness process bug (score-stage skipping read_registry_fields) produced null node_count/chars for all 24 documents in its run, silently defaulting to ERROR status — undetected until a later reconciliation caught it.

RFC-025 D4's mandatory pre-publish MinIO re-verification gates one pipeline but does not fix the underlying bug, so the same defect class recurs in any future run or ad-hoc script that does not route through D4. The fabricated corpus report cascade (Run 9 harness ERROR defaults, RFC-015 verdict fabrication, Run 15 storage-format mismatches) undermined the corpus quality evidence base for multiple historical reports.

#### History

- **Chain 17:** block.get('text','') returns 0 for role='table' blocks in BOTH verdict-promotion code and corpus audit diagnostic char-count scoring — self-reinforcing cycle where pipeline bug and audit-tool bug agree.
- **Chain 18:** score-stage never invoked read_registry_fields to consume persisted MinIO metadata after ingestion; silently defaulted all 24 documents to ERROR status with null node_count/chars — undetected until later reconciliation audit.
- **Chain 19:** RFC-025 D4 pre-publish MinIO re-verification is a process workaround, not root-cause fix — gates one pipeline but leaves the scoring-harness bug able to recur.
- **Chain 32:** DOC_STORE_CORPUS_REPORT.md verdict table was fabricated (15 PASS/10 MARGINAL/0 FAIL vs actual 11/12/2); published to Confluence (page 5101387785) as authoritative status; used as baseline for subsequent remediation planning, misdirecting work by claiming false verdicts.

#### Code Evidence

**save_doc_meta** (verdict.py:78-198): _MERGE_FIELDS tuple at lines 153-175 includes 'total_tree_chars' and 'flat_char_count' but these are derived from block.get('text','') upstream.

**upsert_doc** (queries.py:130-184): meta.get('node_count') at line 170 can be None when scorer does not supply it.

Self-reinforcing pattern: both the pipeline's content-volume floor in apply_promotions (verdict.py:423-430, `len(sig.flat_text.strip())`) and the audit scoring use the same text extraction that ignores table-block content.

Memory note: fabricated-corpus-report-2026-07-17.md documents 'DOC_STORE_CORPUS_REPORT.md verdict table was FABRICATED; always verify against MinIO meta.json'.

#### Key Files

- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/registry/queries.py

---

### Zone 5: Dual-Write Consistency Model

**Severity:** HIGH | **Bug count:** 3

Three-way asymmetric consistency model across dual writers: the converters_cli child subprocess writes the MinIO sidecar (save_doc_meta, no write-visibility barrier) while the long-lived worker parent writes Postgres via CAS-authoritative _upsert_registry_row.

When registry_enabled=false or the connection pool is unavailable, the sidecar silently becomes the sole source of truth but with a DEGRADED consistency guarantee. The registry upsert_doc ON CONFLICT SQL unconditionally overwrites verdict with the incoming value; when the sidecar omits the verdict field, meta.get('verdict','') passes empty string, which the CAS guard treats as a valid (lower-priority) verdict that silently replaces existing FAIL rows.

Reconciliation has load-bearing step ordering that a future refactor could easily violate.

#### Mechanism

Asymmetric write-visibility guarantees: save_doc (documents.py:106) and save_flat_doc (documents.py:165) call _confirm_write_visible for read-after-write consistency; save_doc_meta (verdict.py:78-198) explicitly omits the barrier ('eventual consistency' by design, documented at line 193).

A reader racing right after a sidecar write has no positive visibility guarantee. _upsert_registry_row (registry_mirror.py:56-200) has three possible data sources for the same Postgres row: in-memory registry_fields (from child process), MinIO-read artifact fields (via read_registry_fields), and job-context verdict_fields — reconciled via a documented precedence order (verdict_fields > registry_fields > artifact fields) inline in a ~145-line function.

force_verdict_override is deliberately popped from the fields dict (line 168) so it is never persisted as a column. upsert_doc (queries.py:130-184) uses meta.get('verdict','') at line 175 — empty string is a valid SQL value that the ON CONFLICT CAS treats as a verdict, enabling silent overwrite of existing FAIL rows when the sidecar lacks a verdict field.

reconcile_registry_drift has load-bearing step ordering: drain_verdict_retry_queue MUST run BEFORE the MinIO etag diff scan — reordering lets freshly-recovered verdicts get overwritten by stale MinIO reads.

#### History

- **Chain 22:** save_doc_meta deliberately skips _confirm_write_visible barrier; created three-way asymmetric consistency model.
- **Chain 23:** reconcile_registry_drift load-bearing step ordering — drain Redis verdict retry queue BEFORE MinIO etag diff scan.
- **Chain 30:** upsert_doc ON CONFLICT unconditionally overwrites verdict with empty string when sidecar omits it; queryable-count filter (WHERE verdict != 'FAIL') then treats empty string as queryable, resurfacing previously-excluded FAIL documents.

#### Code Evidence

**save_doc_meta** (verdict.py:78-198): line 193 comment 'Zone-4 Phase 3: write-visibility barrier removed.' and line 188 `sidecar["consistency_model"] = "eventual"`. Contrast save_doc (documents.py:106) `_minio_ops._confirm_write_visible(mc, settings.minio_bucket, key)`.

**_upsert_registry_row** (registry_mirror.py:56-200): three data sources at lines 142-155 (registry_fields vs read_registry_fields vs verdict_fields); force_verdict_override popped at line 168 `bool(fields.pop("force_verdict_override", False))`.

**upsert_doc** (queries.py:130-184): meta.get('verdict','') at line 175 — empty string default; _UPSERT_SQL ON CONFLICT verdict CAS at queries.py:91-95 `WHEN ({_VP_EXCLUDED}) >= ({_VP_EXISTING}) THEN COALESCE(NULLIF(EXCLUDED.verdict, ''), doc_registry.verdict)` — NULLIF catches empty string but only when >= priority comparison already passed.

#### Key Files

- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/storage/documents.py
- src/pageindex_mcp/worker/registry_mirror.py
- src/pageindex_mcp/registry/queries.py

---

### Zone 6: Converter Pipeline and Deployment Gap

**Severity:** HIGH | **Bug count:** 3

The PDF converter chain walker silently advances to AGPL-licensed converters on structural (non-transient) failures, conflicting with CLAUDE.md Hard Rule #4. The remote Docling microservice runs a separately-deployed, versionless image with no contract enforcement, so local fixes to normalize.py, garble.py, and the bidi heading guard have zero effect on remotely-routed documents.

Rotation/orientation detection was applied asymmetrically, leaving a residual population of reversed documents undetected.

#### Mechanism

ConverterFailurePolicy (pipeline.py:64-94) classifies failures as transient or structural and checks whether the next converter is AGPL-licensed. BLOCK_AGPL only fires for transient failures when the next converter is AGPL; structural failures always take the WALK branch.

This means a parse error or import failure (structural) in the primary non-AGPL converter silently routes through pymupdf4llm (AGPL-3.0) with only a logger.warning. The remote Docling microservice is a separate deployment with no version-check or contract enforcement — indexer.py never forwards expected_script to the remote converter, so every document routed through it still gets headings unconditionally reversed.

The bidi heading guard (_heading_is_logical_order) exists only in the local working tree with zero commits in git history. The ALLOW_AGPL_FALLBACK config flag (default '1') and PDF_CONVERTER flag together control AGPL exposure, but a docling-primary config can still fall back to AGPL if ALLOW_AGPL_FALLBACK=1.

#### History

- **Chain 1:** _heading_is_logical_order guard exists only in local working tree; git log -S finds it in NO commit; remote Scaleway Docling runs a separately-deployed, versionless image predating this guard; indexer.py:570-572 documents remote path never forwards expected_script.
- **Chain 3:** rotation/orientation detection applied asymmetrically across corpus; STALLED in WAVE3 (3 bugs, no change).
- **Chain 4:** ConverterFailurePolicy WALK branch silently advances to AGPL converters on structural failures; BIDI_ROOT_CAUSE_RFC033 S1.2 documents pdf_markdown_converters() always seeding the chain with pymupdf4llm; under HR4 must be closed regardless of whether it fired.

#### Code Evidence

**ConverterFailurePolicy** (pipeline.py:64-94): WALK='walk' docstring 'Applied for structural failures (original behavior) and for transient failures when the next converter is non-AGPL.' BLOCK_AGPL='block_agpl' docstring 'Block the chain walk because the next converter is AGPL-licensed and the failure was transient.' Key gap: structural+AGPL combination falls through to WALK.

Config flags: PDF_CONVERTER (config.py, default 'docling') and ALLOW_AGPL_FALLBACK (config.py, default '1') — compliance audits must check both together.

#### Key Files

- src/pageindex_mcp/converters/pipeline.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/config.py

---

### Zone 7: Erasure Cascade (Manually-Maintained Manifest)

**Severity:** MEDIUM | **Bug count:** 2

The right-to-erasure cascade (CLAUDE.md Hard Rule #2) is driven by _ERASURE_MANIFEST, a manually-maintained module-level tuple of ErasureStep entries with no mechanical derivation from the storage-write functions. Adding a new write path (save_doc, save_flat_doc, save_doc_meta, _stage_to_minio) requires a corresponding erasure step, but this coupling exists only in developer awareness.

The registry delete was originally fire-and-forget, logging 'cascade succeeded' regardless of completion.

#### Mechanism

_ERASURE_MANIFEST (documents.py:551+) is a 10-step tuple (uploads, processed_json, processed_flat_json, figures, verdicts, meta_json, redis_cache, reconcile_etag, hash_cache, registry, preloaded) that must mirror every MinIO prefix, Redis key, Postgres table, and filesystem cache that the ingestion pipeline writes to.

When the preloaded/<filename> write path was added for raw uploads, no corresponding erasure step existed — discovered only by manual audit (ISS-41), not by construction. delete_doc (documents.py:178-265) iterates the manifest sequentially and logs gaps between required and completed steps, but there is no compile-time or test-time assertion that the manifest covers all write paths.

The fire-and-forget registry delete (ISS-02) was later wrapped in asyncio.wait_for(timeout=settings.registry_delete_timeout_s) at documents.py:516-519, but the underlying DELETE query in registry still has no statement/connection timeout of its own (ISS-40), so the wait_for timeout is a backstop around a query that can hang indefinitely.

#### History

- **Chain 20:** preloaded/<filename> prefix was missing from erasure manifest; every 'successful' erasure request for such a document silently left the raw preloaded object in MinIO; discovered only by audit (ISS-41).
- **Chain 21:** delete_doc originally dispatched registry delete as fire-and-forget (no await); logged 'cascade succeeded' regardless of completion (HR2 violation, ISS-02); later wrapped in asyncio.wait_for but underlying query has no statement timeout (ISS-40).

#### Code Evidence

**_ERASURE_MANIFEST** (documents.py:551+): 10-step tuple of ErasureStep entries. Each entry has name, step number, description, execute coroutine, and optional required flag. The preloaded step (ISS-41 addition) at documents.py:615-620 `ErasureStep(name="preloaded", step=7, ..., execute=_erase_preloaded, required=False)`.

**_erase_registry** (documents.py:510-529): `asyncio.wait_for(_registry_delete_doc(ctx.doc_id), timeout=settings.registry_delete_timeout_s)` — TimeoutError caught and recorded in ctx.errors but does not abort cascade.

**delete_doc** (documents.py:178-265): manifest-driven iteration with completeness check at lines 234-245 comparing ctx.completed against required/optional steps.

#### Key Files

- src/pageindex_mcp/storage/documents.py
- src/pageindex_mcp/registry/queries.py

---

## Cross-Cutting Themes

1. **Silent degradation defeats the gate:** Recovery/fallback mechanisms (Latin-tessdata OCR substitution, blind bidi flip, AGPL converter fallback, image-enrichment clamp bypass) produce 'false-clean' output that slips past the exact quality gate designed to catch that failure mode.

2. **Coupled kill-switches and shared kernels:** One flag or function (_OCR_ESCALATION, detect_garble, GATE_TABLE severity ordering) simultaneously serves multiple independently-evolving subsystems, so a fix aimed at one consumer silently changes behavior for the others.

3. **Fixes land locally but never reach production:** RFC-033's bidi heading guard was written but never committed to git; the remote Docling microservice runs a separately-deployed, versionless image predating local converter fixes, so a local patch has zero effect on remotely-routed documents.

4. **Diagnostic/audit tooling inherits the pipeline's own structural blind spots:** block.get('text','') scoring 0 for table blocks in both the verdict-promotion code and the corpus audit's diagnostic, producing a self-reinforcing cycle where a pipeline bug and an audit-tool bug agree with each other and look like confirmation.

5. **Duplicated implementations drift independently:** _tree_is_garbled vs _flat_text_is_garbled repeat the identical 500-char digit-ratio floor bug; decide_ocr_mode vs decide_ocr_strategy diverge on parameter forwarding; local vs remote bidi normalization run different code versions.

6. **Threshold-tuning ratchet at the verdict boundary:** Widening a threshold reveals previously-masked defects at the new edge; tightening reveals a different set and regresses previously-passing documents — five consecutive RFCs (022, 024, 025, 026, 033) each independently fixed and re-broke the same boundary, and every change invalidates test fixtures calibrated to the prior value.

7. **Detection without wired remediation:** Garble detection correctly fires at verdict stage, but OCR-recovery escalation is gated on a narrower set of early-stage validation reasons, so a correctly-detected garbled document sometimes never reaches the recovery hook; page-coverage OCR-skip fires without a corresponding marker-removal step, leaving literal `<!-- image -->` markers in output.

8. **Process safeguards substitute for root-cause fixes:** RFC-025 D4's mandatory pre-publish MinIO re-verification prevents publishing wrong corpus numbers but doesn't fix the scoring-harness bug that produces them; the bidi coherence gate was left 'enabled' at 0% real sensitivity (null-detector fallacy) as a no-op rather than being fixed.

9. **Manually-maintained enumerations drift out of sync with reality:** The 11-step erasure manifest and the raise-set of gate reasons triggering LowQualityTreeError are hand-maintained lists with no mechanical derivation from the storage-write/gate-evaluation code they're supposed to mirror, and gaps (e.g. missing preloaded/ prefix) are discovered only by audit, not by construction.

10. **Compliance/legal hard rules are satisfied by convention, not by an enforced invariant:** Hard Rule #2 (erasure) and Hard Rule #4 (AGPL) both had 'best-effort' code paths — fire-and-forget registry delete, unconditional converter chain-walk on any failure — standing in for a guarantee CLAUDE.md actually requires, leaving a gap between documented compliance and what the code enforces under failure conditions.

11. **Gate racing / order-of-evaluation determines outcome, not gate correctness:** When two independent quality checks (rtl_reversal terminal-raise vs. flat-path garble gate) can both fire on the same document, only the first one in evaluation order actually executes, and audit conclusions about 'which gate is broken' can be wrong if they don't trace the real firing order.

12. **Verdict-gate and garble-detection zones account for the majority of regression volume and severity:** Zone 1: 11 bugs critical, Zone 2: 7 bugs critical in the latest run, and both regressed further (not improved) in the most recent remediation wave despite dedicated fix attempts — partial implementations reordered existing logic without deleting the legacy bypass paths they were meant to replace.

13. **Garble detection gates systematically missed three corruption classes:** PUA mojibake, Latin substitution, digit junk across multiple runs; attempted fixes (early-exit reordering, sparse-mojibake heuristic) only partially addressed core issue, leaving flat-doc path unprotected.

14. **Image enrichment promotion mechanism bypassed absolute character-count floors:** Allowing zero-content and near-empty documents to persist as PASS/MARGINAL; metric-counting divergence (meta vs persisted) and unresolved content-loss defects masked by friendlier verdict labels across runs.

15. **Hysteresis mechanism (RFC-025 D0) designed to preserve verdicts across reingestion:** Multiple interconnected bugs (missing expected_script threading, script-detection gaps, threshold widening on any prior PASS) combined to flip previously-FAILed garbled documents to PASS.

16. **Verdict-label softening (FAIL→MARGINAL) across runs 8-9 created false impression of improvement:** Masking unresolved content-loss defects (page rotation, OCR never-fires, table fragmentation); numeric tally improvements driven by label softening and promotion-path bypasses, not actual remediation.

17. **Registry verdict overwrite via ON CONFLICT default-empty-string mechanism:** Allowed reconciliation to silently reset FAIL-verdict documents to queryable state; SQL unconditional-overwrite + missing-field default-to-empty string designed as separate safeguards but neither caught the interaction.

18. **Gate ordering and check precedence repeatedly masked root causes:** Node-count/depth checks running before garbling meant structural defects suppressed text-quality feedback; reordered-tree check added pre-persistence but classify_verdict wiring incomplete, breaking downstream verdict reasoning.

19. **Fabricated corpus report published to Confluence and used as baseline for remediation planning:** Misdirecting subsequent work by claiming false verdicts and closure of issues actually still open; no ground-truth re-validation before trusting metrics across sessions.

20. **Flat-document routing path (FLAT-03) bypasses all text-quality gates:** Allowing corrupted documents (digit-junk, mojibake) to persist as PASS with zero validation.

---

## Recommendations for Future Audit Phases

1. **Root-cause remediation over process safeguards:** Fix the shared-kernel and detection-to-remediation coupling before adding more monitoring.
2. **Mechanical derivation of critical manifests:** Gate-raise-set and erasure-manifest must be generated programmatically from the storage layer and gate definitions.
3. **Version-gated remote services:** Pin remote Docling deployment to a contract-enforced version; forward all expected parameters from local indexer.
4. **Eliminate duplicated implementations:** Consolidate _tree_is_garbled, _flat_text_is_garbled, and related prongs into a single parameterized kernel.
5. **Decouple kill-switches:** Split _OCR_ESCALATION into page-level and per-picture controls; test independently.
6. **Ground-truth corpus validation:** Before publishing summary stats, verify a random sample against MinIO meta.json and raw storage layers.

---

## Simplification Proposals

### Verdict-Gate Cascade

Core simplification: Collapse the ordered if/elif `apply_promotions` pipeline and the separate garble-defect-promotion override in `validate_tree` into ONE declarative table (severity, condition, verdict-cap, recovery-eligibility) evaluated in a single deterministic pass, so 'source-code order as spec' stops being a hidden invariant. Route every promotion path -- including image-enrichment source_selection -- through the same `_clamp_pass` call so no path can bypass the content-volume/quality caps.

Restructuring steps:
1. verdict.py: delete the nested `_apply_clamp` closure (lines ~430-490) and the `source_selection` bypass at 443-447; replace with a single `_clamp_pass(reason, defect=defect, sig=sig, allow_short_image_text=source_selection and _is_image_enrichment)` call so the exception becomes an explicit, testable parameter of the ONE clamp function instead of a code path that skips it. Net: -35/+15 lines.
2. tree_validation.py: remove the ad-hoc primary_defect override loop (407-421) by encoding garble-class severity precedence directly in gates.py's GATE_TABLE (give GARBLING/NODE_GARBLING a tie-break priority field instead of a downstream re-sort). Net: -15/+8 lines in tree_validation.py, +6 lines (one field) per garble gate row in gates.py.
3. gates.py: add explicit `priority` int to the GATE_TABLE row tuple so severity ties resolve via data, not by a second pass reading the fired list. This also fixes the rtl_reversal-vs-garble-gate race (client.py:1992) by making evaluate_gates responsible for full ordering, not the terminal-raise call site.
4. config.py: document PASS_MAX_LEAF_RATIO hysteresis (RFC-025 D0) as a single named constant table (base + prior_pass_widened) rather than an inline conditional, so widening/tightening is a one-line diff reviewed in one place.
5. Add one regression test file (not new production code) asserting: (a) source_selection never bypasses `_clamp_pass`, (b) garble-class defects always win priority ties, (c) hysteresis widening does not flip a document with detect_garble(is_garbled=True) to PASS.

Historical bug classes prevented: the 38-char PASS-via-image-enrichment-bypass; NODE_COUNT_LOW masking NODE_GARBLING requiring the manual override; hysteresis-band flips exploiting the four-bug chain (script_from_filename None + Latin-gibberish heuristic + hardcoded None + threshold widening) -- once garble class always wins the tie, the hysteresis widening cannot promote a garbled doc regardless of the other three bugs; gate-racing nondeterminism at client.py:1992.

Migration risk: MEDIUM -- this changes verdict outcomes for the corpus subset currently relying on the bypass or the override loop; must re-run the corpus scorer before merge (per CLAUDE.md validate_tree() gate) and diff against the current baseline verdict table to catch newly-FAILed docs. Sequence: (1) add priority field to gates.py with no behavior change (verify identical output), (2) fold tree_validation override into it (verify identical), (3) collapse _apply_clamp bypass last since it changes actual PASS/FAIL outcomes, gated behind a feature flag for one corpus-diagnose cycle before removal.

Estimated effort: 2-3 days engineering + 1 corpus-diagnose cycle for verification.

### Garble Detection Kernel

Core simplification: Make `detect_garble` the ONLY entry point with zero config-driven behavioral modes -- delete `garble_short_text_default` as a hidden global switch and replace it with an explicit `min_reliable_length` parameter each of the 15+ callers passes deliberately, and fix the digit-ratio prong to run unconditionally on short text via the same length-aware two-tier logic already used for the secondary short-text check (399-410) instead of gating the primary prong behind `garble_digit_floor`.

Restructuring steps:
1. garble.py: merge the two digit-ratio code paths (399-403 primary, 404-410 secondary short-text) into one length-scaled threshold function `_digit_ratio_prong(norm, cfg)` that applies a sliding threshold (60% for long text, higher confidence bar for short) rather than an on/off floor gate. Net: -20/+25 lines but removes one entire conditional branch class.
2. garble.py: delete `garble_short_text_default` as an implicit global (grep confirms it's read inside detect_garble, not passed explicitly) and instead require callers to pass `short_text_policy` explicitly; update the ~15 callers (garble.py, tree_validation.py, pictures.py) to pass it, defaulting to the pre-Zone-7 behavior so no functional change lands silently. Net: ~15 one-line caller edits, +1 param on detect_garble signature.
3. pictures.py: keep the `_infer_pf` NFKC-compensation fallback (269-272) but move the underlying `_infer_pf` logic into garble.py itself as a documented, tested public helper (`infer_had_presentation_forms`) rather than a private pictures.py-local fix, since the same NFKC-destruction problem can recur anywhere ScriptContext is constructed. Net: +12 lines (moved, not new), -8 lines from pictures.py.
4. Add an explicit doc comment at the top of garble.py naming the NFKC-before-detect_garble ordering constraint as an invariant, with a one-line assertion in the pipeline's normalization step that logs when Arabic presentation-form loss is detected -- this converts a silent workaround into an observable signal (Prometheus counter, per CLAUDE.md observability).

Historical bug classes prevented: short numeric-junk blobs escaping digit-ratio detection at the length-floor boundary; FLAT-03 route bypassing validate_tree entirely for digit junk (fix requires wiring route_and_extract_flat through the same gate -- see below); the hidden-global-mode-switch class of bug where one config flag silently changes behavior for 15 unrelated callers.
5. Separately (tree_validation.py / client.py FLAT-03 routing): route_and_extract_flat must call the same gate evaluation as the tree path before save -- this is a CLAUDE.md hard-rule violation as-is (validate_tree() must run before save_doc) and should be fixed regardless of the broader simplification, by calling `validate_tree`'s flat-text equivalent (`_flat_text_is_garbled` -- already consolidated into detect_garble) unconditionally before any flat save.

Migration risk: MEDIUM-HIGH -- 15+ call sites touched; risk is a missed caller silently keeping old defaults. Sequence: (1) add explicit param with default matching current implicit behavior (no behavior change, verify with existing tests), (2) migrate callers one module at a time (pictures.py, then tree_validation.py, then remaining), running corpus-diagnose after each, (3) only then land the digit-ratio threshold merge since that DOES change detection outcomes, (4) land FLAT-03 gate wiring last as a discrete, reviewable hard-rule-compliance fix.

Estimated effort: 4-5 days engineering + 2 corpus-diagnose cycles (one for the mechanical caller migration, one for the digit-ratio threshold behavior change).

### OCR Recovery Cascade

Core simplification: Replace the single `ocr_escalation_enabled` boolean (which conflates PER_PICTURE and page-level escalation) with two named flags derived from one config source, and unify the three narrower `_eligible_*` predicates with GATE_TABLE's `recovery_eligible` field so the gate table itself is the single source of truth for what triggers recovery -- eliminating the deteciton/remediation gap where reported reason and recovery-eligible set can diverge.

Restructuring steps:
1. gates.py: since each GATE row already carries `recovery_eligible` and `recovery_fns` (per evidence: GARBLING -> _eligible_garble, NODE_COUNT_LOW -> _eligible_low_content), make `decide_ocr_strategy` in picture_plane.py consume THIS table directly instead of maintaining its own separate ordered if-chain of eligibility checks. Net: picture_plane.py -40/+15 lines (delete duplicated eligibility logic), gates.py +0 (already has the fields).
2. picture_plane.py: split `ocr_escalation_enabled` into `per_picture_ocr_enabled` and `page_level_ocr_enabled`, both defaulting to the current combined value so no behavior changes on rollout, then let callers that only want one control it independently. Net: +6 lines (two params replacing one), touches ~3-4 call sites.
3. ocr_langs.py: fix the all-Latin-fallback bug so `ensure_tessdata` explicit non-Latin-request-with-empty-available path also raises `TessdataUnavailableError` (matching the non-Latin-missing-script path already fixed at 128-131/183-188) instead of silently substituting `['deu','eng']` -- this is a straightforward 5-10 line fix, not a restructuring.
4. pictures.py: fix `_recover_picture_results` to skip dense-filling an empty PictureResult when the underlying OCR call produced nothing, and make `splice_figure_markers` neutral-marker fallback emit an explicit, greppable sentinel (e.g. `<!-- OCR_UNRECOVERED -->`) instead of literal `<!-- image -->`, so downstream consumers/audits can distinguish 'no image was here' from 'image OCR failed'. Net: +8 lines.

Historical bug classes prevented: detection-reports-one-thing / recovery-triggers-another mismatches from duplicated eligibility logic; the single-kill-switch class where disabling PER_PICTURE silently also disabled page-level escalation; the marker-removal gap masking failed OCR as an empty image; the all-Latin-fallback silently substituting wrong languages for genuinely non-Latin requests.

Migration risk: LOW-MEDIUM -- eligibility-table consolidation is largely a refactor (same logic, one source), the two-flag split is behavior-preserving by construction (same default), the marker sentinel change and tessdata raise ARE behavior changes that need a corpus-diagnose pass to confirm no doc that previously silently 'passed' via the deu/eng fallback now correctly fails loud.
Sequence: (1) consolidate eligibility onto GATE_TABLE with identical behavior first, verify, (2) split the flag, verify, (3) land the tessdata raise-instead-of-fallback and marker sentinel fixes together as a discrete behavior-changing patch with corpus-diagnose before/after diff.

Estimated effort: 3 days engineering + 1 corpus-diagnose cycle for the behavior-changing patch.

### Measurement and Audit Self-Reinforcing Blind Spot

Core simplification: Extract the block-to-char-count extraction (currently duplicated as `block.get('text','')` in both the pipeline's content-volume floor and the independent audit tool) into ONE shared function that also accounts for table-block content, and delete the audit tool's re-implementation entirely so it calls the pipeline's function -- removing the possibility that both sides share the same blind spot while looking independent.

Restructuring steps:
1. Add `count_block_chars(block)` to a shared helper module (e.g. helpers/text_extraction.py, new ~20-line file) that handles table blocks (concatenating cell text, not just `.get('text','')` which is empty for TABLE-typed blocks) as well as normal text blocks.
2. verdict.py: replace the inline `len(sig.flat_text.strip())` / block.get('text','') pattern in apply_promotions (423-430) with a call to `count_block_chars` summed over blocks. Net: -6/+3 lines.
3. Audit/scoring tool (wherever `block.get('text','')` is duplicated for corpus scoring -- locate via grep in the corpus-diagnose/audit scripts): delete the local re-implementation and import `count_block_chars` from the shared module. This is the highest-value change: it makes the audit tool structurally incapable of sharing the pipeline's blind spot, since a real fix to table-block counting fixes both simultaneously and a regression in one is a regression in both, which is now visible.
4. Fix the scoring-harness process bug (score-stage skipping read_registry_fields) by making `upsert_doc`/scorer defensive: if `node_count`/`chars` come back None, the harness must fail loud (raise) rather than silently default to ERROR status masking the true cause -- 5-10 line change, add an assertion.
5. queries.py: `meta.get('node_count')` at line 170 -- add an explicit None-check that logs a warning distinguishable from a genuine zero, rather than storing None silently.

Historical bug classes prevented: table-heavy documents scoring 0 chars in both pipeline and audit simultaneously (the core self-reinforcing blind spot); the Run 9 harness ERROR-default-for-24-docs incident; the fabricated corpus report cascade class of bug where audit numbers cannot be trusted because they share the measurement bug they're meant to catch.

Migration risk: LOW for the shared-function extraction (behavior-preserving, same counting logic just deduplicated) but MEDIUM for the table-block fix itself since it changes actual char counts for table-heavy docs, which can flip PASS/FAIL verdicts near the content-volume floor threshold -- must run corpus-diagnose before/after and treat any newly-passing table-heavy doc as expected-and-desired (it fixes an undercounting bug) rather than a regression.
Sequence: (1) extract shared function with byte-identical current (non-table-fixed) behavior, delete audit duplication, verify both sides now agree by construction, (2) land the table-block content fix as a separate, reviewed, corpus-diagnosed change, (3) land the harness None-check/fail-loud fix last since it's purely defensive and independent.

Estimated effort: 2 days engineering + 1 corpus-diagnose cycle for the table-block counting fix specifically.

### Dual-Write Consistency Model

Core simplification: Make write-visibility symmetric by default -- add the `_confirm_write_visible` barrier to `save_doc_meta` behind the same code path `save_doc`/`save_flat_doc` already use, deleting the 'eventual consistency by design' special case, and collapse the three-source precedence reconciliation in `_upsert_registry_row` into a single ordered-merge helper function so the precedence order (verdict_fields > registry_fields > artifact fields) is enforced by one small utility instead of inline ~145-line logic.

Restructuring steps:
1. storage/verdict.py: remove the `consistency_model = "eventual"` special-casing (182, 188-193) and call `_minio_ops._confirm_write_visible` after the sidecar write, matching documents.py's pattern. Net: -8/+4 lines. If a real latency reason justifies skipping the barrier here (worth checking with whoever wrote the RFC-025 D4 comment), keep it but make it an explicit, named, tested exception rather than an asymmetric default -- either way, the goal is the asymmetry becomes a documented, deliberate choice, not an incidental one.
2. worker/registry_mirror.py: extract a single `_merge_by_precedence(verdict_fields, registry_fields, artifact_fields) -> dict` pure function (property-testable) implementing the documented precedence order, replacing the inline reconciliation scattered through the ~145-line `_upsert_registry_row`. Net: -60/+35 lines (new pure function + call site, minus removed inline logic).
3. Keep `force_verdict_override` popped-before-persist behavior but move the pop into the new merge function with an explicit comment/test asserting it's never a persisted column -- currently this is an easy-to-miss side effect buried in a 145-line function.
4. registry/queries.py: fix `meta.get('verdict', '')` (175) to `meta.get('verdict') or None`, and make the ON CONFLICT CAS SQL rely on a real NULL rather than the empty-string/NULLIF workaround, removing the silent-overwrite risk directly at the source instead of relying on NULLIF as a downstream patch. Net: 3-5 line change plus one SQL clause simplification (drop the NULLIF wrapper once callers never pass '').
5. Add regression test: reconcile_registry_drift step ordering (drain_verdict_retry_queue before MinIO etag scan) -- convert this from an implicit ordering dependency (comment-only) into an explicit assertion/guard in code (e.g. a sequence marker or a single orchestrating function that cannot be called out of order), not just a docstring warning.

Historical bug classes prevented: read-after-write races on sidecar metadata; silent overwrite of FAIL verdicts by empty-string defaults; drift from step-reordering in reconcile_registry_drift; the general 'three data sources, precedence remembered only in comments' class of bug.

Migration risk: LOW for the pure-function extraction (behavior-preserving refactor, verify via property tests against captured real inputs). MEDIUM for adding the write-visibility barrier to save_doc_meta (latency/throughput impact on the sidecar-write hot path — check RFC-025 D4's rationale for skipping it before changing) and for the empty-string-to-None fix in queries.py (verify no caller currently relies on empty-string-verdict-always-loses-to-NULLIF-fallback as intentional behavior).
Sequence: (1) extract _merge_by_precedence with identical output, verify via replay of recent registry_mirror inputs, (2) fix queries.py verdict None-handling with a test asserting FAIL is never silently overwritten by empty string, (3) evaluate save_doc_meta write-visibility barrier last, behind a flag, measuring latency impact before defaulting it on -- this is the one that must satisfy CLAUDE.md's cascading-erasure guarantee too, since erasure purge order (MinIO uploads -> processed json -> meta.json -> Redis -> backup) depends on writes being visible when the next step checks for them.

Estimated effort: 2-3 days engineering + explicit sign-off on the save_doc_meta barrier tradeoff (latency vs consistency) before enabling it by default.
