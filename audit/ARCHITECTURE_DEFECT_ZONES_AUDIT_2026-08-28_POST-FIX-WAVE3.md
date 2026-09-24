# Architecture Defect Zones Audit — 2026-08-28 POST-FIX-WAVE3

**Date:** 2026-08-28  
**Run:** POST-FIX-WAVE3  
**Scope:** 8 critical/high/medium severity zones across ingestion pipeline, verdict gate, OCR escalation, converter chain, storage consistency, and audit tooling.

---

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|------|----------|-----------|-----------|
| 1 | Verdict-Gate Threshold / Promotion / Override Cascade | **Critical** | 8 | verdict.py, types.py, gates.py, config.py |
| 2 | Garble Detection Cross-Cutting Kernel | **Critical** | 7 | garble.py, pictures.py, tree_validation.py, recovery.py |
| 3 | OCR Recovery Cascade and Kill-Switch Conflation | **High** | 6 | picture_plane.py, pictures.py, indexer.py, recovery.py |
| 4 | Converter Chain Fallback and AGPL Gating | **High** | 4 | indexer.py, pipeline.py, normalize.py |
| 5 | Dual-Writer Verdict Persistence and Consistency Model Split | **High** | 4 | verdict.py, documents.py, registry_mirror.py, reconcile.py, queries.py |
| 6 | Bidi/RTL Processing Split (Local vs. Remote) | **High** | 3 | normalize.py, gates.py, garble.py, indexer.py |
| 7 | Erasure Cascade and Storage Consistency Drift | **Medium** | 2 | documents.py, queries.py, verdict.py |
| 8 | Measurement/Audit Tooling Shared Blind Spots | **Medium** | 4 | indexer.py, verdict.py, helpers/verdict.py |

**Total:** 38 bugs attributed across 8 zones | **Critical:** 2 | **High:** 4 | **Medium:** 2

---

## Zone Details

### Zone 1: Verdict-Gate Threshold / Promotion / Override Cascade

**Severity:** Critical | **Bug count:** 8

#### Mechanism

The verdict computation pipeline (evaluate_gates → apply_promotions → compute_verdict) is an order-dependent, first-match-wins cascade with threshold boundaries, promotion overrides, and bypass flags (source_selection, image_enrichment_promoted) that systematically generate regressions:

- **Threshold widening** masks defects at the new edge; **tightening** regresses previously-passing docs
- Each **promotion path** can bypass content-volume floors that other paths enforce
- Five consecutive RFCs (022, 024, 025, 026, 033) each fixed and re-broke this same boundary
- The **hysteresis band** added to stabilize borderline documents reclassified zero-content failures from FAIL to MARGINAL, **violating CLAUDE.md Hard Rule #5** (never silently persist low-quality trees)

#### History

- **Chain 12:** PASS_MAX_LEAF_RATIO widened 0.17→0.30 allowed 81-garbled-node docs to PASS
- **Chain 13:** Hysteresis reclassified zero-content as MARGINAL, violating Hard Rule #5
- **Chain 14:** image_enrichment_promoted bypassed content-volume floor allowing 38-char docs to PASS
- **Chain 15:** Hardening produced 12 corpus regressions
- **Chain 16:** Every threshold change invalidated test fixtures calibrated to prior value
- **Chains 26–27:** Five RFCs hit this exact boundary repeatedly

#### Code Evidence

**verdict.py:379–466** — `apply_promotions`: 
- D1 structural hard-fail gate (line ~400: `sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio`) runs BEFORE promotions
- _try_image_enrichment inside it returns `_apply_clamp(_ie)` which, when `source_selection=True`, **bypasses the inner clamp entirely** (lines 410–413)
- D2 ordered chain (_try_image_enrichment → _try_structural_pass → _try_ocr_promotion → _try_flat_promotion → _try_content_class_promotion → _try_small_doc_promotion → MARGINAL fallback) is **pure source-order specification**

**evaluate_gates:124–222** — 
- Uses `_GATE_PRIORITY` tiebreak (line 201: `min(_masked, key=lambda d: _GATE_PRIORITY.get(d, len(GATE_TABLE)))`) to suppress co-firing defects
- Determines whether promotions run at all — adding/removing a defect from HARD_FAIL_DEFECTS changes which documents reach apply_promotions

**config.py** — Threshold values (PASS_MAX_LEAF_RATIO, MARGINAL boundaries) are module-level constants with no versioning or audit trail of changes

#### Key Files

- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/helpers/types.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/config.py

---

### Zone 2: Garble Detection Cross-Cutting Kernel

**Severity:** Critical | **Bug count:** 7

#### Mechanism

`detect_garble` is a single shared kernel with **13 callers across 9+ distinct subsystems** (text-layer probing, tree validation, flat-block checks, Tesseract recovery, image-enrichment verdict promotion, keep-best OCR comparison, converter pre-garble probe, tree conversion). A narrow fix to one prong silently changes behavior for all other callers. Blind spots (numeric-junk, Latin mojibake from Arabic OCR) propagate simultaneously to every downstream gate:

- **Config flag override:** `config.garble_short_text_default` forces `is_garbled=True` for text<200 chars unconditionally (RFC-025 D2), creating a hidden mode switch
- **NFKC normalization destroys signal:** Arabic presentation-form codepoints (U+FB50-FEFF) are normalized away BEFORE text reaches detect_garble
- **ScriptContext threading gap:** Not all callers supply ScriptContext correctly — `_text_layer_has_content` constructs ScriptContext with `had_presentation_forms=False` when `script_context is None`

#### History

- **Chain 2:** NFKC normalization destroys the bidi coherence detector's ONLY failure signal
- **Chain 5:** Latin language tessdata silently substituted producing mojibake that passes every prong
- **Chain 6:** token_repetition fix left wider blast radius unaddressed
- **Chain 7:** GATE_TABLE severity ordering lets node_count_low suppress garbling reason preventing OCR recovery
- **Chain 17:** table block.get('text','') returns 0 sharing blind spot with audit tooling
- **Chain 25:** Each RFC iteration narrowed scope creating escape hatches
- **Chain 29:** Zone 4 confirmed throwaway ScriptContext bug

#### Code Evidence

**garble.py:494–572** — `detect_garble`:
- Lines 525–530: short_text_prior_garble override (blob_kind==RAW_MARKDOWN, config.garble_short_text_default, len(blob)<200, original_defect ∈ garbling/node_garbling → force is_garbled=True)
- Lines 543–554: presentation-forms fallback where `_had_pf=True` when `_arc>0` and `_pf==0` and `_effective_script=='Arabic'` — covering the NFKC destruction gap

**garble.py:318–405** — `garble_prongs`:
- 11 independent prongs including digit_ratio (line 379), token_repetition (line 388, tokens filtered to isalnum), and latin_gibberish (line 393)
- Each prong can independently fire; ordering determines priority

**pictures.py:240–272** — `_text_layer_has_content`:
- Constructs ScriptContext with `had_presentation_forms=False` when `script_context is None` (lines 269–272)
- Breaks the garble-detection contract that NFKC destruction is accounted for

**garble.py:622–713** — `_garble_check_nodes`:
- Per-node + whole-tree concatenated fallback with recursive descent

#### Key Files

- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/client/recovery.py

---

### Zone 3: OCR Recovery Cascade and Kill-Switch Conflation

**Severity:** High | **Bug count:** 6

#### Mechanism

Three independently-evolving concerns (page-level OCR escalation, per-picture crop OCR, image-enrichment promotion) are conflated under shared decision points and kill-switches. Detection fires without corresponding remediation being wired:

- **decide_ocr_strategy** uses ordered gate chain where **sequence IS specification** (re-entry guard MUST precede UNIFIED_OCR_PLAN_ENABLED)
- **decide_ocr_mode** is a legacy wrapper that historically dropped document_type and ocr_langs parameters
- **Single _OCR_ESCALATION kill-switch** gates BOTH page-level and per-picture OCR with no independent control
- **Detection-to-remediation gap:** Garble detection can fire at verdict stage, but OCR recovery is gated on narrower set of early-stage validation reasons
- **Page-coverage OCR-skip** fires without marker-removal step, leaving literal `<!-- image -->` markers in output

#### History

- **Chain 7:** GATE_TABLE severity ordering lets node_count_low suppress garbling reason preventing OCR escalation
- **Chain 8:** Page-coverage filter skips OCR but leaves markers in output
- **Chain 9:** Per-picture OCR conflates with page-level escalation degrading retrieval
- **Chain 10:** _OCR_ESCALATION kill-switch gates both paths unconditionally
- **Chain 11:** decide_ocr_mode dropped document_type/ocr_langs parameters
- **Chain 24:** Standalone .jpg/.png missing OCR capture

#### Code Evidence

**picture_plane.py:357–430** — `decide_ocr_strategy`:
- Lines 389–395: re-entry guard MUST run first (Zone-2 fix comment)
- Lines 404–411: UNIFIED_OCR_PLAN_ENABLED+image-type short-circuit runs AFTER guard
- Ordered if-chain where sequence determines behavior

**picture_plane.py:438–469** — `decide_ocr_mode`:
- Thin wrapper returning only .mode field
- Now forwards document_type/ocr_langs (Zone-8 fix at lines 460–468) but has only 1 caller
- Does not match all call sites where decide_ocr_strategy would be more appropriate

**pictures.py:240–272** — `_text_layer_has_content`:
- Feeds has_image_markers/skip-OCR signal consumed by decide_ocr_strategy's per-picture escalation gate

**indexer.py:537–551** —
- force_full_page decision made pre-conversion
- Per-picture decision deferred to post-conversion
- Creates temporal split in decision surface

#### Key Files

- src/pageindex_mcp/picture_plane.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/client/recovery.py

---

### Zone 4: Converter Chain Fallback and AGPL Gating

**Severity:** High | **Bug count:** 4

#### Mechanism

The converter chain walk treats all failures uniformly, with a ConverterFailurePolicy classification that allows **structural failures to silently advance to AGPL-licensed converters**. Remote Docling microservice runs separately-deployed image predating local converter fixes (bidi heading guard never committed, expected_script not forwarded), creating **local-vs-remote code divergence** that no test can catch:

- Structural failures still allow walking to AGPL converters (only logging warning) — **violates CLAUDE.md Hard Rule #4**
- Remote Docling service runs independently-versioned image with no contract enforcement (no version assertion or script field in payload)
- Local fixes to normalize.py or garble.py have zero effect on remotely-routed documents

#### History

- **Chain 1:** RFC-033 bidi heading guard was never committed to git
- **Chain 3:** Rotation detection applied asymmetrically across corpus
- **Chain 4:** Unconditional chain-walk on any failure silently activates AGPL conversion with only logger.warning
- **Chain 29:** Converter-gate-route ordering entanglement required Zone 5 refactor

#### Code Evidence

**indexer.py:441–914** — `_convert_to_tree`:
- Lines 560–565: converter chain walk loop iterates ConverterChainEntry instances
- Lines 576–600: failure-mode classification via `_classify_transient_failure`
- Lines 609–625: ConverterFailurePolicy decision: RETRY for transient+under-limit, BLOCK_AGPL for transient+next-is-agpl, REJECT for end-of-chain, **WALK otherwise** (allows advancing to AGPL)
- Lines 570–572: NOTE that remote path does NOT forward expected_script to external Docling microservice

**pipeline.py:682–770** — `pdf_markdown_converters`:
- Builds converter chain with is_agpl flags
- Lines 641, 656: AGPL_FALLBACK_TOTAL metric shows operator awareness
- No hard gate beyond ConverterFailurePolicy classification

#### Key Files

- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/converters/pipeline.py
- src/pageindex_mcp/converters/normalize.py

---

### Zone 5: Dual-Writer Verdict Persistence and Consistency Model Split

**Severity:** High | **Bug count:** 4

#### Mechanism

Two independent writers (save_doc_meta from isolated converters_cli child subprocess, _upsert_registry_row from long-lived worker parent) target overlapping verdict fields for same doc_id across different process boundaries. **Consistency model is split:**

- Postgres documented authoritative (CAS + RETURNING, max-priority-wins per RFC-037 D5)
- MinIO sidecar is passive archive
- save_doc/save_flat_doc retain `_confirm_write_visible` barrier
- save_doc_meta **deliberately omits** barrier (eventual consistency)
- When registry_enabled=false or pool unavailable, sidecar silently becomes sole source of truth with degraded consistency
- reconcile_registry_drift cron has **load-bearing ordering**: drain Redis verdict retry queue BEFORE MinIO etag diff, or freshly-recovered verdicts get overwritten by stale MinIO reads

#### History

- **Chain 20:** Erasure manifest missing preloaded/ prefix discovered only by audit (ISS-41)
- **Chain 21:** Registry-delete was fire-and-forget logging success on silent failure
- **Chain 22:** save_doc_meta barrier removal created asymmetric consistency across three writers
- **Chain 23:** registry_enabled=false silently changes consistency model at runtime

#### Code Evidence

**verdict.py:78–197** — `save_doc_meta`:
- Lines 84–89: document 'eventual' consistency with **no** `_confirm_write_visible` barrier
- Contrast with documents.py save_doc line 106 which retains barrier
- Line 186: stamps consistency_model='eventual'

**registry_mirror.py:55–164** — `_upsert_registry_row`:
- Lines 87–93: pool-not-ready fallback to sidecar-only with `_enqueue_verdict_retry`
- Lines 136–145: best-effort sidecar backfill from Postgres winning row

**reconcile.py:109–228** —
- Line 156: `_drain_verdict_retry_queue` runs BEFORE MinIO etag diff scan (line 163–170)
- **Load-bearing ordering** to prevent overwriting recovered verdicts with stale MinIO reads

#### Key Files

- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/storage/documents.py
- src/pageindex_mcp/worker/registry_mirror.py
- src/pageindex_mcp/registry_backfill/reconcile.py
- src/pageindex_mcp/registry/queries.py

---

### Zone 6: Bidi/RTL Processing Split (Local vs. Remote)

**Severity:** High | **Bug count:** 3

#### Mechanism

RTL/bidi text processing is **split across local and remote code paths running different versions** of the same logic:

- **RFC-033 bidi heading guard** (_heading_is_logical_order) designed but **never committed to git**
- Remote Scaleway Docling microservice runs **separately-deployed image predating local converter fixes**
- Documents routed remotely still get headings unconditionally reversed
- Bidi coherence gate (_check_bidi_coherence) measured at '0 violations' — **null-detector fallacy**: NFKC normalization destroys Arabic presentation-form codepoints (U+FB50-FEFF) that are detector's ONLY failure signal
- Run-selector counts only U+0600-06FF (excluding presentation forms)

#### History

- **Chain 1:** bidi heading guard never committed to git — 0 occurrences of _heading_is_logical_order in HEAD
- **Chain 2:** bidi coherence '0 violations' was null-detector fallacy due to NFKC destroying failure signal
- **Chain 3:** Rotation detection applied asymmetrically leaving residual undetected reversed documents

#### Code Evidence

**normalize.py** —
- _heading_is_logical_order has 0 occurrences in src/ (search_code confirms, only audit/ references exist)

**gates.py:126–175** — `_gate_bidi_degraded`:
- References `_check_bidi_coherence` at line 145

**garble.py:549–554** —
- detect_garble presentation-forms fallback: when `_arc>0` and `_pf==0` and `_effective_script=='Arabic'`, assume `had_presentation_forms=True`
- Acknowledges that NFKC normalization destroys the signal

**indexer.py:570–572** —
- Documents known gap: remote path does NOT forward expected_script to external Docling microservice

#### Key Files

- src/pageindex_mcp/converters/normalize.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/client/indexer.py

---

### Zone 7: Erasure Cascade and Storage Consistency Drift

**Severity:** Medium | **Bug count:** 2

#### Mechanism

Right-to-erasure cascade (CLAUDE.md Hard Rule #2) is driven by manually-maintained `_ERASURE_MANIFEST` tuple enumerating every storage prefix. When new ingestion routes add locations, manifest **drifts out of sync**. Discovered missing only by audit (ISS-41):

- preloaded/<filename> prefix was missing
- Erasure cascades across MinIO, Redis, Postgres, hash-cache stores with **ordered steps**
- Registry-delete step historically was **fire-and-forget** (logging success on silent failure)
- Still lacks statement timeout on underlying DELETE query (ISS-40)
- Asymmetric consistency model (save_doc with barrier vs. save_doc_meta without) means erasure can race with concurrent write

#### History

- **Chain 20:** preloaded/ prefix missing from manifest (ISS-41)
- **Chain 21:** registry-delete fire-and-forget, now has asyncio.wait_for timeout but DELETE query lacks statement timeout (ISS-40)

#### Code Evidence

**documents.py:551–618** — `_ERASURE_MANIFEST`:
- 11 ErasureStep entries: uploads→processed_json→processed_flat_json→figures→verdicts→meta_json→redis_cache→reconcile_etag→hash_cache→registry→preloaded
- preloaded step (lines 539–544) was ISS-41 addition
- Each step has name, step number, description, execute coroutine, optional required=False flag
- Manifest is module-level tuple constant with **no mechanical derivation** from storage-write functions (save_doc, save_flat_doc, save_doc_meta, _stage_to_minio)

#### Key Files

- src/pageindex_mcp/storage/documents.py
- src/pageindex_mcp/registry/queries.py
- src/pageindex_mcp/storage/verdict.py

---

### Zone 8: Measurement/Audit Tooling Shared Blind Spots

**Severity:** Medium | **Bug count:** 4

#### Mechanism

Corpus audit/scoring tooling **inherits the same structural blind spots** as the pipeline it measures. Char-count scoring via `block.get('text','')` is 0 for role='table' blocks in BOTH verdict-promotion code and corpus audit's diagnostic. Scoring harness had process-integrity bug where score-stage never invoked path to consume persisted MinIO metas, defaulting all 24 documents to ERROR with null node_count/chars:

- Audit tooling and pipeline share same content-measurement primitives (block.get('text',''), flat_char_count, node_count)
- No independent ground-truth oracle
- Table blocks store content in rows/cells, not in 'text' field
- Audit harness process bug: defaulted entire corpus run to ERROR
- RFC-025 D4 mandatory pre-publish MinIO re-verification is **process workaround, not root-cause fix**
- Self-reinforcing cycle: bug in pipeline → low measurement → audit confirms low number → operator trusts audit

#### History

- **Chain 17:** Synthetic-structure builder measures content via block.get('text','') inheriting blind spot from audit diagnostic
- **Chain 18:** Scoring harness defaulted entire corpus to ERROR without detection until reconciliation
- **Chain 19:** Pre-publish verification is process workaround not root-cause fix
- **Chain 28:** Run 9–15 claims of improvement were refuted by later audits

#### Code Evidence

**indexer.py** — Synthetic-structure content builder:
- Measures content for verdict promotion using block.get('text','')

**verdict.py + helpers/verdict.py** —
- block.get('text','') pattern yields 0 for role='table' blocks
- Affects both synthetic-structure builder and corpus audit harness
- Scoring harness process bug (chain 18): score-stage never invoked read_registry_fields to consume persisted MinIO metas after ingestion

**Memory note: fabricated-corpus-report-2026-07-17.md** —
- Confirms this pattern of audit-tooling coupling to pipeline blind spots

#### Key Files

- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/helpers/verdict.py

---

## Cross-Cutting Themes

1. **Silent degradation defeats the gate:** Recovery/fallback mechanisms (Latin OCR substitution, blind bidi flip, AGPL converter fallback, image-enrichment promotion) produce 'false-clean' output that slips past the quality gate designed to catch that exact failure mode.

2. **Coupled kill-switches and shared kernels:** One flag or function (_OCR_ESCALATION, detect_garble, GATE_TABLE severity ordering) simultaneously serves multiple independently-evolving subsystems, so a fix aimed at one consumer silently changes behavior for the others.

3. **Fixes land locally but never reach production:** RFC-033 bidi heading guard was never committed; remote Docling microservice runs separately-deployed, versionless image predating local converter fixes, so local patch has zero effect on remotely-routed documents.

4. **Diagnostic/audit tooling inherits structural blind spots:** Char-count scoring via block.get('text','') is 0 for table blocks in BOTH verdict-promotion code and corpus audit diagnostic; scoring-harness process bug defaulted entire corpus run to ERROR without anyone noticing until reconciliation.

5. **Duplicated implementations drift independently:** _tree_is_garbled vs _flat_text_is_garbled repeat identical digit-ratio floor bug; decide_ocr_mode vs decide_ocr_strategy silently diverge in parameter passing; local vs remote bidi normalization run different code versions.

6. **Threshold-tuning ratchet at verdict boundary:** Widening threshold reveals previously-masked defects at new edge; tightening reveals different set and regresses previously-passing docs — five consecutive RFCs (022, 024, 025, 026, 033) each fixed and re-broke this same boundary; every change invalidates test fixtures calibrated to prior threshold.

7. **Detection without wired remediation:** Garble detection fires correctly at verdict stage, but OCR-recovery escalation is gated on narrower set of early-stage validation reasons, so correctly-detected garbled document never reaches recovery hook; page-coverage OCR-skip fires without marker-removal step, leaving literal `<!-- image -->` markers in output.

8. **Process safeguards substitute for root-cause fixes:** RFC-025 D4 mandatory pre-publish MinIO re-verification prevents publishing wrong corpus numbers but does not fix scoring-harness bug that produces them; null-detector bidi gate left 'enabled' at 0% sensitivity as no-op, deferring real fix.

9. **Manually-maintained enumerations drift:** 11-step erasure manifest, raise-set of gate reasons triggering LowQualityTreeError drift out of sync with actual storage-write/gate-evaluation code as new ingestion routes and gate reasons are added, discovered missing only by audit rather than by construction.

10. **Compliance/legal hard rules satisfied by convention:** HR2 erasure cascade, HR4 AGPL satisfied by best-effort code paths (fire-and-forget registry delete, unconditional converter chain-walk on any failure) rather than enforced invariant, leaving gap between what CLAUDE.md requires and what code actually guarantees under failure conditions.

11. **Gate interdependencies:** Garble detection → OCR escalation → verdict promotion. Each RFC narrowed detection scope, creating bypasses later RFCs had to close.

12. **Converter-Gate-Route ordering entanglement:** Re-promotion logic, containment depth, garble checks became tightly coupled across module boundaries, requiring Zone 5 refactor to untangle.

13. **Audit report fabrication cascade:** Run 9 harness ERROR defaults, RFC-015 verdict fabrication, Run 15 storage format mismatch and refuted 'improvements' undermined corpus quality evidence base.

14. **Verdict classification threshold fragility:** 0.17 boundary introduced without hysteresis band; borderline documents flipped verdicts across runs; coupled to garble-gate ordering, creating hidden gate-ordering dependencies.

15. **ScriptContext threading gaps:** _gate_node_garbling builds throwaway ScriptContext with hardcoded had_presentation_forms=False, breaking garble-detection contract and forcing Zone 4/5 refactor to thread context properly through recovery/indexer layers.

---

## Recommendations (Out of Scope)

This audit documents defect zones as structured observations. Remediation priorities, RFC design, and implementation sequencing are outside this audit's scope but should address:

- Zone 1 (threshold cascade) requires formal threshold versioning and test-fixture auto-calibration
- Zones 2–3 (detection/remediation) require detection-to-gate wiring integrity checks
- Zone 4 (AGPL gating) requires hard-fail policy on structural failures
- Zone 5 (dual-writer) requires transactional coordination or single writer
- Zones 6–8 (split/drift) require derivation mechanics replacing manual enumeration

---

## Simplification Proposals

### Verdict-Gate Threshold / Promotion / Override Cascade

Core simplification: Replace the ordered if/elif promotion chain in apply_promotions (verdict.py:379-466) with a single declarative table of (predicate, promotion_result, clamp_policy) rows evaluated by one generic loop, so 'first match wins' becomes an explicit, inspectable priority column instead of an implicit consequence of function-definition order. Fold _apply_clamp's content-volume floor into the table itself as a per-row flag (clamp_bypass: bool) rather than a special-cased branch per helper, eliminating the silent source_selection=True bypass. Delete classify_verdict's 55-caller backward-compat wrapper by migrating callers to the table-driven evaluator directly (or keep it as a 3-line passthrough, not a second code path).

Concrete steps: (1) verdict.py — introduce PROMOTION_TABLE: list[PromotionRule] with explicit `priority: int` and `clamp_bypass: bool` fields near the top of the file (~+40 lines); (2) collapse _try_image_enrichment/_try_structural_pass/_try_ocr_promotion/_try_flat_promotion/_try_content_class_promotion/_try_small_doc_promotion into pure predicate+result functions with no clamp logic inside them (~-120 lines net, each shrinks to a guard+return); (3) apply_promotions becomes a single `for rule in sorted(PROMOTION_TABLE, key=priority): if rule.predicate(sig): return clamp(rule.result) if not rule.clamp_bypass else rule.result` (~-60 lines); (4) evaluate_gates' _GATE_PRIORITY dict (gates.py:201) moves next to HARD_FAIL_DEFECTS in config.py so both live in one reviewable ordering surface instead of two files; (5) grep all 55 classify_verdict call sites, repoint to the table evaluator, delete or thin the wrapper (types.py touch, ~-20 net once wrapper is trivial).
File targets: verdict.py (net ~-140 lines), gates.py (~-10, priority dict relocated not deleted), config.py (~+15), types.py (wrapper trim ~-20).

Historical bug classes prevented: (a) 'reordering a _try_* helper silently changes verdicts' — the table's explicit priority column makes reordering a diffable, reviewable change instead of an accidental side effect of function placement; (b) 'clamp bypass via source_selection=True slipping past the content-volume floor' — clamp_bypass becomes a named, grep-able field instead of an implicit code path, so future promotions can't accidentally inherit the bypass; (c) 'threshold change invalidates uncorrelated test fixtures' — thresholds move to one table so a fixture failure maps to one row, not a chain-order regression hunt; (d) the hysteresis-band MARGINAL misclassification of zero-content failures (CLAUDE.md Hard Rule #5 violation) becomes visible as a table row with an explicit predicate that can be unit-tested in isolation against the zero-content case.

Migration risk: Medium. The table must reproduce the exact current first-match-wins semantics or verdicts silently shift for edge cases (e.g., docs that currently fall through multiple _try_* calls). Sequence: (1) add the table alongside the existing chain, assert equivalence by running both over the full snapshot corpus and diffing verdicts (no behavior change yet); (2) once diffed clean, delete the old chain in one commit; (3) migrate classify_verdict callers in a separate follow-up commit so the wrapper removal is isolated and revertable. Never combine (2) and (3).

Estimated effort: 3-4 days including corpus-diff validation.

