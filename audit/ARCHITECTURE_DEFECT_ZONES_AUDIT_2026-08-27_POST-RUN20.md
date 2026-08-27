# Architecture Defect Zones Audit — 2026-08-27 POST-RUN20

**Date:** 2026-08-27  
**Run:** POST-RUN20  
**Prepared by:** Architecture Review  

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|---|---|---|---|
| 1 | Verdict Gate Threshold / Promotion Override Cascade | critical | 5 | verdict.py, gates.py, tree_validation.py, config.py |
| 2 | Garble Detection Cross-Cutting Kernel | critical | 5 | garble.py, tree_validation.py, gates.py, ocr_langs.py |
| 3 | OCR Recovery Cascade | high | 4 | recovery.py, picture_plane.py, pictures.py, indexer.py |
| 4 | Bidi/RTL Processing Split | high | 3 | normalize.py, indexer.py, gates.py, recovery.py |
| 5 | Measurement / Audit Tooling Shared Blind Spots | high | 3 | verdict.py, garble.py, gates.py, registry_mirror.py |
| 6 | Converter Chain Fallback / AGPL Gating | medium | 2 | pipeline.py, indexer.py, config.py |
| 7 | Erasure Cascade / Storage Consistency | high | 2 | documents.py, verdict.py, registry_mirror.py, queries.py |

**Total Zones:** 7 (2 critical, 4 high, 1 medium)  
**Total Attributed Bugs:** 24  

---

## Zone Details

### Zone 1: Verdict Gate Threshold / Promotion Override Cascade

**Severity:** critical | **Bug count:** 5

#### Mechanism

The verdict-computation pipeline (compute_verdict → evaluate_gates → apply_promotions) contains layered threshold parameters and competing promotion paths whose interactions create a ratchet: every 'softening' change (widen PASS_MAX_LEAF_RATIO, add hysteresis) reveals masked defects, and every 'hardening' change (add floor checks, tighten gates) causes verdict-label regressions across the corpus. Five consecutive RFCs (024, 025, 026, 022, 033) each introduced or revealed bugs at this boundary. The image-enrichment promotion (priority=100) explicitly overrides the structural hard-fail check, creating a two-tier override where evaluate_gates can suppress all of apply_promotions, but within apply_promotions the image-enrichment candidate can suppress what would otherwise be a structural FAIL — order-of-evaluation matters and is guarded by _has_image_rescue rather than by re-running evaluate_gates.

Fixing any threshold at this boundary shifts the verdict distribution, revealing defects that the prior setting masked:
- a. Widening PASS_MAX_LEAF_RATIO from 0.17 to 0.30 allowed documents with 81 garbled nodes to PASS (chain 10).
- b. Adding hysteresis reclassified zero-content extraction failures from FAIL to MARGINAL, violating HR5 (chain 11).
- c. The image_enrichment_promoted path bypassed content-volume floors, allowing 38-char documents to PASS (chain 12).
- d. Hardening these same gates produced 12 corpus regressions as previously-masked defects became visible (chain 14).
- e. Each threshold change invalidated test fixtures written to the prior boundary, causing test failures that looked like code bugs but were measurement-calibration drift (chain 13).

#### Code Evidence

`compute_verdict` at verdict.py:516-559 dispatches evaluate_gates then apply_promotions. `apply_promotions` at verdict.py:402-513 collects candidates from `_try_image_enrichment` (priority=100), `_try_structural_pass`, `_try_ocr_promotion`, `_try_flat_promotion`, `_try_content_class_promotion`, `_try_small_doc_promotion`; `max(candidates, key=priority)` wins. The image-enrichment override is at verdict.py:462-471: `_has_image_rescue = any(c.path_name == "image_enrichment_promoted" for c in candidates); if not _has_image_rescue and sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio: return FAIL`. `_try_image_enrichment` at verdict.py:220-265 checks content_class, image_enrichment_ratio >= 0.8, total_chars >= min_image_promoted_chars, and detect_garble (pass-through). `evaluate_gates` at verdict.py:119-217 resolves hard-fail via HARD_FAIL_DEFECTS and _GATE_PRIORITY tiebreak.

#### Key Files
- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/config.py

---

### Zone 2: Garble Detection Cross-Cutting Kernel

**Severity:** critical | **Bug count:** 5

#### Mechanism

detect_garble is the single most cross-cutting decision primitive in the codebase (11 direct callers, hop-2 fan-out into apply_promotions, _execute_ocr_retry, _attempt_tesseract_raster_recovery, tree_validation). It functions as a shared kernel feeding three independently-evolving subsystems: tree-quality gating, OCR-skip decisions for picture regions, and OCR-retry keep-best arbitration. Its heuristic prongs (garble_prongs) have complementary structural blind spots: CMap-corrupted German text passes latin_gibberish when expected_script='Latn'; digit-ratio is diluted below 60% by markdown formatting symbols; token_repetition fires false-positive on legitimate tables with pipe/currency symbols. The tessdata language-fallback path silently substitutes Latin OCR for Arabic, producing mojibake that passes every prong. Any change to detect_garble's threshold logic has wide, only-partially-visible blast radius across all three consumer subsystems.

The generative mechanism operates through fan-out from a single decision surface with prong-level blind spots:
- a. When tessdata silently substitutes ['deu','eng'] for missing Arabic traineddata, the resulting Latin mojibake passes all prongs — not PUA, not glued mixed-script, not digit-heavy, rarely hits 30% token repetition (chain 5).
- b. The duplicate _tree_is_garbled/_flat_text_is_garbled implementations repeat the 500-char digit-ratio floor independently, so a fix in one does not propagate to the other (chain 8).
- c. Token_repetition fired false-positive on tables with pipe/currency symbols; the fix (exclude non-alphanumeric tokens) did NOT address numeric-junk or Latin-script mojibake that still pass undetected (chain 15).
- d. validate_tree's GATE_TABLE evaluates garbling first (severity=0), but the signal computation means a minimal-tree garbled document gets reason='node_count_low' (severity=1) instead of 'garbling', and OCR escalation only fires for reason in ('garbling','node_garbling'), so recovery never triggers (chain 18).
- e. The bidi coherence check's presentation-form signal is destroyed by NFKC normalization before it runs, making it a zero-sensitivity null detector (chain 4).

#### Code Evidence

`detect_garble` at garble.py:494-572 delegates to garble_prongs after short-circuit checks. `garble_prongs` at garble.py:318-405 implements 9 prongs: digit_ratio gated by `if len(norm) > cfg.garble_digit_floor` (line 380), token_repetition at `if (most_common_count / len(tokens)) > 0.30` (line 386), latin_gibberish gated by garble_latin_gibberish_enabled. `ensure_tessdata` at ocr_langs.py:92-196 now raises TessdataUnavailableError for non-Latin missing traineddata (Zone-3 fix), but Latin languages still silently dropped. `validate_tree` at tree_validation.py:262-354 iterates GATE_TABLE; GATE_TABLE at gates.py:321-408 places GARBLING at severity=0 and NODE_COUNT_LOW at severity=1.

#### Key Files
- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/converters/ocr_langs.py

---

### Zone 3: OCR Recovery Cascade

**Severity:** high | **Bug count:** 4

#### Mechanism

_execute_ocr_retry is the single densest sequential-cascade-where-order-matters gate in the codebase: 234 lines, cyclomatic complexity 20, with fixes from at least 5 zone remediation waves (Zone-1/2/6/7/8) layered into one function. It is the shared tail for three independent recovery paths (_recover_garble_ocr, _recover_low_content_ocr, _recover_image_dominant_ocr), each with per-method eligibility checks but shared execution. The keep-best heuristic has 4 sequential stages where each only runs if the prior did not decide. The OCR-mode decision surface retains a split: decide_ocr_strategy (picture_plane.py:357-430) is the 'unified' successor, but decide_ocr_mode (now a thin wrapper, picture_plane.py:438-458) is called by _recover_picture_results (pictures.py:1102) without passing document_type or ocr_langs, losing Zone-8 parameters. Detection-without-remediation gaps exist: garble detection fires at the verdict stage but no OCR recovery is wired to that output path.

The generative mechanism operates through accreted complexity in an ordering-dependent cascade:
- a. D1's coverage-filter (skip OCR when >60% page area) has no matching marker-removal step, so deliberately-skipped regions leave literal `<!-- image -->` markers in output (chain 1).
- b. The _OCR_ESCALATION kill-switch gates BOTH page-level OCR escalation and per-picture crop OCR, so toggling it for one behavior disables the other (chain 2).
- c. The keep-best cascade's strict ordering means adding a new stage at the wrong position overrides existing conclusions without detection by single-stage tests (chain 2).
- d. The garble gate correctly fires on verdict-stage garbling, but OCR escalation is wired only to early-stage validation failures — detection landed, recovery hook missing (chain 20).
- e. Large-file processing dies mid-flight with no artifacts persisted and no diagnostic data (chain 21).

#### Code Evidence

`_execute_ocr_retry` at recovery.py:83-316 implements the full cascade. Keep-best stages at recovery.py:241-290: (a) Zone-8 zero-char shortcut: `if pre_retry.total_chars == 0 and post_retry_chars > 0: retry_wins = True`, (b) char-count: `elif post_retry_chars < pre_retry.total_chars: retry_wins = False`, (c) equal-count garble tiebreak calls detect_garble on pre/post (line 256-268), (d) RFC-029 D4 density: `_density_improved = _post_density < _pre_density * 0.80`. `decide_ocr_mode` at picture_plane.py:438-458 now delegates to decide_ocr_strategy but `_recover_picture_results` (pictures.py:1102) calls it without document_type/ocr_langs kwargs. trace_path confirms sole caller is _recover_picture_results.

#### Key Files
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/picture_plane.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/client/indexer.py

---

### Zone 4: Bidi/RTL Processing Split

**Severity:** high | **Bug count:** 3

#### Mechanism

Bidi/RTL text normalization is applied at multiple independent sites (local converter pipeline via _pre_inference_normalize, remote Docling microservice, post-conversion _renormalize_bidi_guarded, per-node _recover_rtl_repair) with no version synchronization between them. The remote Docling service runs a separately-deployed image that imports the same reconstruct_bidi_order but may predate local fixes. The bidi coherence gate (_gate_bidi_degraded, severity=6 in GATE_TABLE, _ReasonPolicy.CAP_MARGINAL, recovery_waived=True) derives from the former _check_bidi_coherence that was historically a null detector. Rotation-detection checks added by RFC-026 D2 are applied asymmetrically across the corpus.

The generative mechanism operates through the same normalization function running independently in two deployable units with no shared version:
- a. The RFC-033 D2 Part A heading-order guard was reportedly never committed to git, and the remote Docling service runs a stale image — so documents routed through the remote path get headings unconditionally reversed (chain 3).
- b. The bidi coherence check's signal (presentation-form codepoints U+FB50-FEFF) is destroyed by NFKC normalization BEFORE the check runs, making it a zero-sensitivity detector; BIDI_COHERENCE_ENFORCE=true was promoted based on '0 violations' that was actually 0% true-positive rate (chain 4).
- c. Rotation detection catches some RTL reversals but not others asymmetrically (chain 19).

Fixing bidi at one deployment site (local) has no effect on the other (remote), and the coherence gate cannot detect what NFKC normalization has already erased.

#### Code Evidence

`reconstruct_bidi_order` at converters/normalize.py:78-126 (16 inbound callers per CBM). `_pre_inference_normalize` at normalize.py:129-161 calls it at lines 138 and 147. `_renormalize_bidi_guarded` at client/indexer.py:113-151 calls it at line 143. `_gate_bidi_degraded` at gates.py:126-157. GATE_TABLE at gates.py:321-408 confirms BIDI_DEGRADED as GateSpec(TreeDefect.BIDI_DEGRADED, _ReasonPolicy.CAP_MARGINAL, severity=6, recovery_waived=True). Remote path at client/indexer.py:460-472 does not forward expected_script; renormalization conditional on pipeline_config.remote_md_renormalize.

#### Key Files
- src/pageindex_mcp/converters/normalize.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/client/recovery.py

---

### Zone 5: Measurement / Audit Tooling Shared Blind Spots

**Severity:** high | **Bug count:** 3

#### Mechanism

The diagnostic and measurement tooling used to audit corpus quality inherits the same structural blind spots as the pipeline it measures, or has its own process-integrity bugs that produce misleading data. Char-count scoring via block.get('text','') misses table blocks entirely because they carry content in headers/rows/row_records, not in a 'text' key. The scoring harness had a process-integrity bug where the score-stage never consumed persisted MinIO metas after ingestion succeeded, defaulting all 24 docs to ERROR with null node_count/chars. The RFC-025 D4 'pre-publish verification' protocol (mandatory live re-pull from MinIO before publishing any audit figures) became a critical safeguard but is a process workaround, not a root-cause fix.

The generative mechanism operates through measurement tools making the same structural assumptions as the code they audit, creating a feedback loop where both the defect and the measurement are blind to the same data:
- a. RFC-022's B1-Fix and the corpus audit's own char-count diagnostic both sum block.get('text',''), which returns 0 for every table block — so table-heavy documents appear to have catastrophically low content, and a fix aimed at promoting them inherits the identical blind spot (chain 7).
- b. The scoring harness never consumed persisted MinIO metas, defaulting all 24 documents to ERROR — completely masking the true corpus state (chain 16).
- c. RFC-025 D4 pre-publish verification became mandatory practice but does not fix the harness bug — it prevents publication of false results based on that bug (chain 17).

The meta-problem is that 'zero violations' or 'all ERROR' measurements are taken at face value when they reflect detector/harness failures, driving wrong remediation decisions.

#### Code Evidence

`save_doc_meta` at storage/verdict.py:78-185 shows _MERGE_FIELDS defining persisted sidecar fields. `TreeSignals.from_tree` computes flat_text by flattening tree node text, which ignores table block structure (headers/rows/row_records carry content, not block['text']). `_try_image_enrichment` at verdict.py:243 computes total_chars = len(_dedupe_chart_text_lines(sig.primary_text)). The harness reads verdict from MinIO sidecar via read_registry_fields, but the scoring stage failed to invoke this path, defaulting to ERROR.

#### Key Files
- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/worker/registry_mirror.py

---

### Zone 6: Converter Chain Fallback / AGPL Gating

**Severity:** medium | **Bug count:** 2

#### Mechanism

pdf_markdown_converters() builds an ordered converter chain gated by allow_agpl_fallback and PDF_CONVERTER. When the primary converter (Docling) fails or times out, the for-loop in _convert_to_tree walks to the next chain entry unconditionally. If that entry is pymupdf4llm (AGPL-3.0), an unplanned outage silently becomes AGPL-licensed network-served conversion. The allow_agpl_fallback flag now blocks pymupdf4llm from the chain entirely when false, and AGPL_FALLBACK_TOTAL metrics track when it fires. However, the chain is a flat list with no policy for 'which fallbacks are acceptable for which failure modes' — a timeout is treated identically to a parse error. Converter provenance (extraction_route, converter_name) is now persisted in the sidecar via _MERGE_FIELDS, but historical corpus documents lack this data.

The generative mechanism operates through unconditional chain-walking on converter failure:
- a. When remote Docling raises HTTP 504 on a large PDF, _convert_to_tree walks to pymupdf4llm — an AGPL route the operator may not have intended, violating HR4's framing as 'a legal decision to clear, not a settled safe-harbor' (chain 6).
- b. The remote Docling service runs a separately-deployed image that may predate local fixes, so converter-level fixes have no effect on documents routed through the remote path (shared with bidi zone).
- c. The underlying structural issue is that the chain treats all failures equivalently: a transient network timeout and a fundamental parsing incompatibility both trigger the same fallback path.

#### Code Evidence

`pdf_markdown_converters` at converters/pipeline.py:571-641: `if pipeline_config.allow_agpl_fallback: chain.append(("pymupdf4llm", _pdf_to_markdown_no_pics, False))` then docling inserted at position 0 or appended based on PDF_CONVERTER. `_convert_to_tree` at client/indexer.py:435-540: `chain = pdf_markdown_converters()` then `for idx, (conv_name, conv_fn, _conv_supports_ocr) in enumerate(chain): try: ... except Exception as conv_exc: md_content = None`. AGPL_FALLBACK_TOTAL.labels(reason='fired').inc() at indexer.py:~580 when used_converter == 'pymupdf4llm' and not primary.

#### Key Files
- src/pageindex_mcp/converters/pipeline.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/config.py

---

### Zone 7: Erasure Cascade / Storage Consistency

**Severity:** high | **Bug count:** 2

#### Mechanism

The HR2 right-to-erasure cascade (delete_doc) is driven by _ERASURE_MANIFEST, a tuple of 11 ErasureStep entries. Each new storage prefix or derived store requires adding a corresponding step to the manifest, but the manifest is not mechanically derived from the storage-write code paths — it is a manually maintained list that drifts when new ingestion routes add storage locations. The verdict-write architecture has three named entry points (write_verdict → save_doc_meta; save_doc_meta; _upsert_registry_row → upsert_doc) into overlapping state with asymmetric durability: save_doc/save_flat_doc retain write-visibility barriers, save_doc_meta deliberately removed them (Zone-4 Phase 3). When registry_enabled is false or pool unavailable, the sidecar becomes the sole source of truth, changing the effective consistency model.

The generative mechanism operates through decoupled storage-write paths with a manually-maintained erasure manifest:
- a. The preloaded/<filename> prefix was added by a later ingestion path but the erasure cascade was designed against the original prefix set — the step was added only after audit discovery (chain 9).
- b. The registry-delete step was historically fire-and-forget, so delete_doc logged 'full cascade succeeded' even when the Postgres row delete silently failed (chain 9, now fixed: _erase_registry uses asyncio.wait_for).
- c. The asymmetric write-visibility barrier between save_doc (retained) and save_doc_meta (removed) means reading the sidecar immediately after a verdict write has a weaker consistency guarantee than reading a tree artifact.
- d. New storage locations (figures/<doc_id>/*, verdicts/<sha256>.json) had to be added retroactively, each time discovered by audit rather than by construction.

#### Code Evidence

`_ERASURE_MANIFEST` at storage/documents.py:510-581 lists 11 ErasureSteps. `delete_doc` at documents.py:145-224 iterates: `for entry in _ERASURE_MANIFEST: reached = await entry.execute(ctx)` with completeness check at lines 200-215 logging missed_required and missed_optional. `_erase_registry` at documents.py:452-488 uses `await asyncio.wait_for(_registry_delete_doc(ctx.doc_id), timeout=settings.registry_delete_timeout_s)`. `save_doc_meta` at verdict.py:176-177 documents: 'Zone-4 Phase 3: write-visibility barrier removed'. `_upsert_registry_row` at registry_mirror.py:55-155 performs CAS upsert with verdict_fields overlay and best-effort sidecar backfill.

#### Key Files
- src/pageindex_mcp/storage/documents.py
- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/worker/registry_mirror.py
- src/pageindex_mcp/registry/queries.py

---

## Cross-Cutting Themes

1. **Silent degradation defeats the very gate it feeds:** OCR/text-recovery fallbacks (tessdata language substitution, Latin gibberish from Arabic OCR, canonical bidi reversal invisible to _check_bidi_coherence) each independently produce 'false-clean' content that sails through the garble/bidi verdict gates the pipeline relies on for quality control — the recovery mechanism and the quality gate were designed without a shared model of each other's failure modes.

2. **Coupled kill-switches and shared code paths make one RFC's fix disable another RFC's mechanism:** _OCR_ESCALATION gates both page-level OCR escalation and per-picture crop OCR simultaneously; the AGPL pymupdf4llm fallback silently substitutes for a failing remote Docling call; a single _pre_inference_normalize/reconstruct_bidi_order runs both locally and inside a separately-deployed remote microservice with no version sync.

3. **Fixes exist only in the working tree or only in the wrong deployment target, so production keeps running the pre-fix behavior:** the RFC-033 D2 Part A bidi guard was never committed and the remote Docling service image predates it entirely; RFC-018's D1 text-layer probe sat uncommitted while D0 landed; RFC-022's B3 diagnosis found the audit's own tooling shared the exact blind spot (table blocks invisible to char-count) that the fix it was validating also had.

4. **Diagnostic/measurement tooling inherits the same structural blind spots as the pipeline it audits:** char-count scoring (RFC-022 table blocks), 'zero violations' bidi measurement read as a false-positive-rate proxy when it was actually a zero-sensitivity null detector, and missing extraction provenance (no converter name/route/build-sha persisted anywhere) forced multiple RFCs to reconstruct root causes by hand from indirect fingerprints (table-separator style, HTTP 504 patterns) rather than direct evidence.

5. **Duplicated/parallel implementations of the same safety mechanism drift independently:** _tree_is_garbled vs _flat_text_is_garbled repeat the identical 500-char digit-ratio floor as separate code, and compliance cascades (erasure, ZDR routing, AGPL gating) are re-derived per new code path (preloaded/ prefix, standalone-image branch, remote-vs-local converter chain) instead of centralized, so new ingestion routes routinely fall outside older Hard-Rule enforcement until a later audit finds the gap.

6. **Gate threshold changes masked real defects under the appearance of verdicts while underlying extraction failures remained unfixed:** Each RFC threshold tuning shifted the verdict distribution, revealing previously-masked defects and causing test fixture invalidation; the ratchet effect means that even correct fixes at one threshold reveal bugs at the adjacent boundary.

7. **Detection implemented without corresponding remediation mechanisms:** Garble detected at verdict stage but no OCR escalation hook wired from that path; RTL reversal caught by rotation detection but no corresponding re-extraction initiated; minimal-tree garbled documents misclassified by gate ordering as NODE_COUNT_LOW instead of GARBLING, blocking recovery.

8. **Process-level harness bugs obscured corpus state visibility; RFC-025 D4 pre-publish verification became critical but is a workaround, not a fix:** Scoring harness never consumed persisted MinIO metas, defaulting all 24 documents to ERROR; mandatory re-pull from MinIO before publishing prevented publication of false data but did not fix the underlying harness bug.

9. **Multilayer text-encoding/script defects go undetected or inconsistently detected:** RTL reversal, Presentation Forms, CMap corruption, digit-ratio dilution by markdown, Latin-script mojibake from tessdata fallback — each independently produces content that passes detection gates designed for other failure modes.

10. **RFC-026 gate-hardening fixes applied asymmetrically:** Landscape infographic recognized as valid but portrait twin not; some Arabic rotation defects caught, others skipped from re-ingestion batch — incomplete coverage suggests threshold tuning without systematic corpus sweep.

11. **Large-file/resource limits create silent failures with ambiguous terminal states:** Processing dies mid-flight with no artifacts persisted and no diagnostic data; verdict label changes between runs but underlying failure persists, making root cause attribution impossible.

---

## Simplification Proposals

### Verdict Gate Threshold / Promotion Override Cascade

1. CORE SIMPLIFICATION: Remove the ordering dependency itself: the structural hard-fail check must be evaluated FIRST and unconditionally, and 'image_enrichment_promoted' must become a documented, floor-gated EXCEPTION to that hard-fail rather than a candidate collected before the hard-fail check runs. Replace the six independent _try_* functions (each returning Optional[PromotionCandidate], winner-take-max-priority) with a single ordered pipeline of gate->exception checks, so no promotion path can silently suppress the hard-fail path by mere presence in a list.

2. RESTRUCTURING STEPS:
 - verdict.py:462-471 — Delete the '_has_image_rescue' bypass. Hard-fail check (max_leaf_ratio > th.hard_fail_max_leaf_ratio) moves to the top of compute_verdict, evaluated before ANY _try_* function runs (~15 lines moved, net 0 delta).
 - verdict.py:220-265 — _try_image_enrichment becomes the ONLY function permitted to override an already-computed hard-fail, and it must additionally check sig.node_count/garble status (not just image_enrichment_ratio + total_chars) so a 38-char doc cannot re-enter via this path (~10 line addition of an explicit content-floor AND to the existing condition).
 - Collapse _try_structural_pass, _try_ocr_promotion, _try_flat_promotion, _try_content_class_promotion, _try_small_doc_promotion into a single ordered if/elif chain (not a max(candidates, key=priority) scan) so priority is expressed as source-code order, not a numeric field that can be silently mis-ranked (~-60 lines net: removes PromotionCandidate dataclass priority field and the max() collection dance).
 - config.py — freeze PASS_MAX_LEAF_RATIO and hard_fail_max_leaf_ratio behind a single named constant pair with an inline comment stating the corpus regression history (0.17 vs 0.30), so future threshold edits require touching one place and reading the warning.

3. BUG CLASSES PREVENTED: chain 10 (garbled docs slipping through via widened ratio + rescue-path masking), chain 12 (image_enrichment bypassing content floors), chain 14 (hardening one gate silently changing what other gates can reach) — all stem from the collect-then-max-priority indirection; making order explicit and hard-fail unconditional-except-one-documented-path removes the mechanism.

4. MIGRATION RISK: Medium — verdict distribution WILL shift again since this is a real behavior change (hard-fail is now truly first). Sequence: (a) land the reordering behind a feature flag, (b) run full corpus scoring twice (flag on/off), diff verdicts, (c) triage only NEW garbled-PASS or NEW FAIL-that-was-PASS cases before flipping the flag default, (d) then delete the flag and dead priority-field code. Do NOT touch chain 13's stale fixtures until after the flag flip — recompute fixtures in the same PR that flips the default so they don't rot again.

5. EFFORT: ~2-3 days (1 day reorder + flag, 1 day corpus diff triage, 0.5 day fixture regen, 0.5 day cleanup).

### Garble Detection Cross-Cutting Kernel

1. CORE SIMPLIFICATION: Delete the duplicate _tree_is_garbled/_flat_text_is_garbled implementations and make both call one shared detect_garble(text, cfg) — the digit-ratio floor and all nine prongs live in exactly one place. Also fix the ordering bug in tree_validation.py's GATE_TABLE so garbling is never masked by node_count_low, and move NFKC normalization to AFTER the bidi coherence check reads presentation-form codepoints (or run the check on pre-normalization text).

2. RESTRUCTURING STEPS:
 - garble.py — Identify and remove the duplicate digit-ratio/500-char-floor logic; both tree and flat-text callers route through garble_prongs(text, cfg) with a `is_tree: bool` flag only where node-structure genuinely changes behavior (~-40 lines, one function deleted).
 - tree_validation.py:262-354 / gates.py:321-408 — Reorder or fix reason-assignment so that when BOTH garbling and node_count_low would fire, 'garbling' wins as the surfaced reason (since OCR escalation checks `reason in ('garbling','node_garbling')`), matching the GATE_TABLE's already-intended severity=0 priority. This is a ~5-line fix to whatever short-circuits reason selection before severity ordering is applied.
 - ocr_langs.py:92-196 — Extend TessdataUnavailableError to also fire for missing Latin traineddata that gets silently substituted with ['deu','eng'], closing the chain-5 hole at its source rather than trying to catch it downstream in garble prongs (~15 lines, removes a special-case Latin exemption).
 - garble.py:386 — token_repetition and latin_gibberish prongs already fixed for tables (chain 15); no further zone-generic change needed there beyond the shared-implementation consolidation above.

3. BUG CLASSES PREVENTED: chain 8 (duplicate implementations drifting — fix-one-miss-other), chain 18 (reason misclassification suppressing OCR recovery), chain 5 (silent Latin tessdata substitution producing undetected mojibake), chain 4 (NFKC destroying the bidi signal before it's checked).

4. MIGRATION RISK: Low-medium. The shared-function consolidation is a pure refactor (behavior-preserving by construction — same logic, one call site) and should be a same-day, test-covered change. The reason-ordering fix and tessdata fix are the risky parts since they change corpus verdicts (more docs correctly flagged as garbled). Sequence: consolidate duplicate code first (zero-risk, easy to verify via existing tests), land the tessdata fix second, then the reason-ordering fix last with a corpus diff before merge (same discipline as Zone 1's threshold changes, since these gates feed the promotion cascade in Zone 1).

5. EFFORT: ~2 days (0.5 day dedupe, 0.5 day tessdata fix, 0.5 day reason-ordering fix, 0.5 day corpus verification).

### OCR Recovery Cascade

1. CORE SIMPLIFICATION: Split the single _OCR_ESCALATION kill-switch into two independently-named flags (page-level escalation vs per-picture crop OCR) so toggling one doesn't silently disable the other, and add the missing marker-removal step immediately after the coverage-filter skip so `<!-- image -->` placeholders never leak into output when OCR is deliberately skipped. Wire the garble-gate's verdict-stage detection into the same escalation trigger set used for early-stage validation failures, closing the detection-without-recovery gap.

2. RESTRUCTURING STEPS:
 - recovery.py — Rename/split _OCR_ESCALATION into OCR_PAGE_ESCALATION_ENABLED and OCR_PICTURE_CROP_ENABLED (search all call sites; ~10 line change, config.py gets one new env var).
 - pictures.py — Immediately following the D1 coverage-filter skip (>60% page area), add a step that strips or replaces the `<!-- image -->` marker for skipped regions (~10-15 lines added at the skip site, not a new module).
 - picture_plane.py:438-458 / pictures.py:1102 — Fix the missing document_type/ocr_langs kwargs at the sole caller (_recover_picture_results) — this is a straightforward call-site bug fix, not a redesign (~2 lines).
 - tree_validation.py / recovery.py — Add 'garbling'-reason-from-verdict-stage to the same trigger enum/set that early-stage validation failures use to invoke OCR escalation, so chain-20's detection-without-recovery gap closes (~10 lines, extending an existing set/enum rather than adding new dispatch logic).
 - The 4-stage keep-best cascade (241-290) stays as-is structurally but gets a single docstring/comment enumerating stage order and the invariant each stage assumes, so a future added stage is added with awareness rather than blind insertion at the wrong position — documentation, not code restructuring (~15 comment lines, 0 logic delta).

3. BUG CLASSES PREVENTED: chain 1 (leaked markers), chain 2 (single kill-switch coupling unrelated behaviors), chain 20 (garble detection landing without a recovery hook), and the kwarg-mismatch bug at the sole caller.

4. MIGRATION RISK: Low — these are largely independent, additive, or call-site-scoped fixes (split a flag, add a marker-strip step, fix a kwarg call, extend a trigger set) with no cross-cutting reordering. Each can land and be tested separately; only the garble-trigger-wiring change needs a corpus diff since it will cause more docs to enter OCR retry.

5. EFFORT: ~1.5 days (0.5 day flag split + marker fix, 0.25 day kwarg fix, 0.5 day garble-trigger wiring + corpus check, 0.25 day docstring).

### Bidi/RTL Processing Split

1. CORE SIMPLIFICATION: Eliminate the two-deployment-site split by making reconstruct_bidi_order's behavior version-stamped and checked at call time (remote Docling service reports its bidi-fix version in its response; local code refuses to trust remote heading order if the version is stale), and fix the bidi coherence check to run BEFORE NFKC normalization so its presentation-form signal isn't destroyed before it can fire.

2. RESTRUCTURING STEPS:
 - normalize.py:129-161 — Reorder _pre_inference_normalize so the bidi-coherence read of presentation-form codepoints (U+FB50-FEFF) happens before NFKC folding, not after; NFKC continues to run afterward for downstream consumers (~10-15 line reorder, no new logic).
 - client/indexer.py:460-472 — Forward expected_script to the remote path (currently not forwarded) so remote-side normalization has the same input local normalization gets; this closes the drift between the two deployment sites without needing to merge them into one (~5 lines, pass an existing parameter through).
 - gates.py:126-157 (_gate_bidi_degraded) — Since the signal was proven zero-sensitivity (0% true-positive), re-baseline BIDI_COHERENCE_ENFORCE=true against the corrected pre-NFKC signal before trusting it further; do not add new gate logic, just recompute against the fixed detector (0 line delta, a corpus re-run).
 - Do NOT attempt to merge the remote and local bidi implementations into one service in this pass — that's a larger infra change outside 'simplest sustainable restructuring'; instead, add a version-check assertion so a stale remote image fails loudly (raises/logs) instead of silently reversing headings (~15 lines in client/indexer.py at the remote call site).

3. BUG CLASSES PREVENTED: chain 4 (NFKC destroying the detection signal before the check runs, producing a false '0 violations' promotion), and chain 3's silent-staleness failure mode (a stale remote image now fails loudly rather than silently reversing headings) — full elimination of chain 3 itself requires actually redeploying the remote image, which is an ops action, not a code restructuring.

4. MIGRATION RISK: Medium — the normalization reorder changes what BIDI_COHERENCE_ENFORCE actually measures, likely producing MORE detected violations (the detector goes from zero-sensitivity to functional), which will change verdicts for RTL/mixed-script documents. Sequence: (a) land the reorder behind the existing BIDI_COHERENCE_ENFORCE flag defaulting to report-only/log mode, (b) run corpus, review the newly-surfaced violations for false positives, (c) flip enforce=true only after triage, (d) separately and independently, verify+redeploy the remote Docling image (ops task, track outside this code change).

5. EFFORT: ~2 days code (0.5 day reorder, 0.5 day expected_script forwarding + version-check assert, 1 day corpus re-baseline/triage) + untracked ops time for the remote redeploy, which is outside this restructuring's control.

### Measurement / Audit Tooling Shared Blind Spots

1. CORE SIMPLIFICATION: Fix the shared blind spot at its single source — TreeSignals.from_tree and any char-counting diagnostic must sum content from table blocks (headers/rows/row_records) not just block.get('text',''), and the corpus scoring harness must be forced to fail loudly (not default-to-ERROR) when it cannot read a persisted MinIO sidecar, so a harness bug can never again masquerade as '24/24 documents failed'.

2. RESTRUCTURING STEPS:
 - Wherever TreeSignals.from_tree flattens tree node text (likely in verdict.py or a shared tree-walk helper) — add table-block content extraction (headers + rows/row_records) to the char-count sum, in ONE place; then every consumer (RFC-022 B1-Fix, the corpus audit diagnostic, _try_image_enrichment's total_chars at verdict.py:243) inherits the fix automatically since they should all be calling the same signal computation rather than re-summing text independently (~20-30 lines added to one function; net negative across the codebase since ad-hoc re-summation call sites can be deleted and replaced with calls to the shared signal).
 - worker/registry_mirror.py / the scoring harness — Replace the silent default-to-ERROR-on-read-failure with an explicit exception/assertion when read_registry_fields fails to find a sidecar it expects to exist, plus a distinct 'harness could not read N of M documents' summary line separate from 'N of M documents scored ERROR' (~15-20 lines, mostly a try/except boundary and a counter).
 - Formalize RFC-025 D4's pre-publish verification as an automated CI check (a script that re-derives the published verdict distribution from MinIO sidecars and diffs against the report before it can be published) rather than a manual practice step — this converts a documented-but-optional habit into an enforced gate (~40-60 lines, one new script + a CI hook, no changes to existing logic).

3. BUG CLASSES PREVENTED: chain 7 (table-block blind spot propagating into both the code fix and its own measurement), chain 16 (harness silently defaulting all docs to ERROR and masking true corpus state), and pre-empts a recurrence of chain 17's need for manual pre-publish verification by making it structurally impossible to publish an unverified report.

4. MIGRATION RISK: Low for the table-block fix (additive, makes char counts strictly larger/more-correct, should only IMPROVE how many docs correctly promote) — but re-run corpus scoring after this change since some FAIL-by-zero-content docs will now show real content. Medium for the harness fail-loud change since it may reveal currently-hidden read failures that block CI; sequence table-block fix first (low risk, immediate value), harness hardening second with a grace-period warning-only mode before making it a hard failure.

5. EFFORT: ~1.5 days (0.5 day table-block fix + corpus re-run, 0.5 day harness fail-loud + counter, 0.5 day CI pre-publish gate script).
