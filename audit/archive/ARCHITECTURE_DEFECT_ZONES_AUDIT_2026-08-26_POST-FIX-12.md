# Architecture Defect Zones Audit — 2026-08-26 POST-FIX-12

**Date:** 2026-08-26  
**Run:** POST-FIX-12  
**Scope:** PageIndex MCP server — ingestion pipeline, verdict engine, extraction converters, OCR subsystem, and cross-process consistency  

---

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|------|----------|-----------|-----------|
| 1 | Garble Detection Surface Fragmentation | critical | 10 | garble.py, normalize.py, pictures.py, ocr_langs.py, script.py |
| 2 | Verdict Gate Promotion Bypass Cascade | critical | 8 | verdict.py, config.py, storage/verdict.py |
| 3 | OCR Pipeline Flag Conflation and Re-entry Hazards | critical | 7 | picture_plane.py, recovery.py, pictures.py, config.py |
| 4 | Content-Destructive Heuristic Chains | high | 6 | normalize.py, tree_split.py |
| 5 | Verdict Persistence Competing Writers | high | 5 | verdict.py, registry_mirror.py, queries.py, indexer.py |
| 6 | Landscape/Rotation and Remote Route Divergence | high | 5 | normalize.py, picture_plane.py, recovery.py, config.py |
| 7 | Image Block Conflation and Marker Survival | medium | 4 | picture_plane.py, pictures.py, indexer.py |
| 8 | Verified-Locally-Never-Deployed Fix Drift | medium | 4 | normalize.py, registry_mirror.py, ocr_langs.py |

**Total bugs attributed:** 49  
**Critical severity zones:** 3  
**High severity zones:** 3  
**Medium severity zones:** 2

---

## Zone Details

### Zone 1: Garble Detection Surface Fragmentation

**Severity:** critical | **Bug count:** 10

#### Mechanism

The garble detection pipeline is structurally fragmented across multiple dimensions where signal destruction occurs before detection instruments can inspect the signals. The generative mechanism is ordering-dependent signal destruction combined with self-referential inference:

1. NFKC normalization in `_pre_inference_normalize` (converters/normalize.py:157, converters/pictures.py:167) destroys Arabic Presentation Forms (U+FB50-FEFF) BEFORE garble_prongs or bidi detectors can inspect them, nulling the presentation_forms prong signal for canonical Arabic text.

2. `expected_script` is self-inferred from potentially-already-corrupted text via `_infer_script`, so the gate never fires on text that is already garbled.

3. The Latin-gibberish check in garble_prongs only fires when `expected_script` is non-Latin, so CMap-corrupted German documents bypass all Latin-script garble heuristics.

4. `ensure_tessdata` silently falls back to `['deu','eng']` when no requested languages are available, so an Arabic OCR-escalation request runs Latin-only OCR producing garbled Latin mojibake that still passes the garble gate.

5. The garble_prongs digit_ratio check is gated behind a configurable floor that lets short numeric-junk blobs pass uninspected.

Each fix attempt targeting one prong is defeated by the NFKC ordering destroying the codepoints before the new prong runs.

#### History

- **RFC-010 D3B:** Added `_flat_text_is_garbled` duplicating `_tree_is_garbled` (fix-one-miss-the-other drift, confirmed root cause of marsoom-13 Latin-mojibake).
- **RFC-013 D7 (ISS-36):** Diagnosed the duplication, unresolved through FIX-11.
- **RFC-028 D2:** Arabic presentation-forms prong caused Human Rights PDF FAIL→ERROR regression.
- **RFC-033 D2:** `_check_bidi_coherence` measured 0% TPR (two independent causes: `_reversed_morphology` fires only on U+FB50-FEFF but `get_display()`-reversed text uses canonical U+06xx; line-selector excludes presentation-form lines).
- **RFC-015/018/026/027:** `expected_script` gap flip-flopped across 6+ runs without closing.
- **ISS-34/marsoom-13:** `ensure_tessdata` silent deu/eng fallback produced exact failure mode.

#### Code Evidence

- **garble_prongs** (garble.py:318-405): digit_ratio check gated behind `if len(norm) > cfg.garble_digit_floor`; latin_gibberish check fires only when `garble_latin_gibberish_enabled` and `expected_script != 'Latn'`.
- **_pre_inference_normalize** (converters/normalize.py:157, converters/pictures.py:167): `text = unicodedata.normalize('NFKC', text)` destroys U+FB50-FEFF codepoints BEFORE garble/bidi detectors run.
- **detect_garble** (garble.py:494-564): `_effective_script = script_context.dominant_script; if _effective_script is None: _effective_script = _infer_script(blob)` — self-inferred from potentially-corrupted text.
- **ensure_tessdata** (converters/ocr_langs.py:91-188): Final fallback `return ['deu', 'eng']` regardless of script requested (lines 186-188).

#### Key Files

- `src/pageindex_mcp/helpers/garble.py`
- `src/pageindex_mcp/converters/normalize.py`
- `src/pageindex_mcp/converters/pictures.py`
- `src/pageindex_mcp/converters/ocr_langs.py`
- `src/pageindex_mcp/script.py`

---

### Zone 2: Verdict Gate Promotion Bypass Cascade

**Severity:** critical | **Bug count:** 8

#### Mechanism

The verdict engine implements a two-stage cascade (evaluate_gates → apply_promotions) where multiple promotion branches bypass structural quality gates, violating Hard Rule 5 ('never silently persist a low-quality tree'). The generative mechanism is a priority-based candidate system where each RFC adds a new promotion path to rescue one category of false-positive FAILs, but each new path also opens a bypass for genuinely-bad documents.

The `image_enrichment_promoted` candidate carries a locked priority=100 that explicitly outranks the structural max_leaf_ratio hard-fail. Small_doc_promotion, flat_promotion, and content_class_promotion candidates each independently bypass content-volume quality checks. Threshold ratcheting across RFCs (PASS_MAX_LEAF_RATIO widened 0.17→0.20→0.30) progressively weakened the structural gates. RFC-025 hysteresis mechanism (prior-verdict anchoring) was then defeated entirely by corpus reingestion wiping processed/*.meta.json sidecars.

#### History

- **RFC-023 D10:** Widened PASS_MAX_LEAF_RATIO 0.17→0.20 (missed Reitlehrer at 0.2571).
- **RFC-024 D0:** Widened 0.20→0.30, own risk table predicted failure.
- **RFC-025 D0:** Hysteresis defeated by corpus reingestion wiping meta.json sidecars; softened four zero-char Arabic docs FAIL/ERROR→MARGINAL.
- **RFC-025 D1:** Page-level `_text_layer_has_content` from header/footer disabled picture OCR (503k→382 chars).
- **Run 9 audit:** Flagged image_enrichment_promoted bypass — documents with only 38-123 chars received PASS verdicts, contravening Hard Rule 5.

#### Code Evidence

- **compute_verdict** (verdict.py:521-564): `outcome = evaluate_gates(...); if outcome.hard_fail_verdict is not None: return outcome.hard_fail_verdict` — hard-fail short-circuit.
- **apply_promotions** (verdict.py:407-518): `_has_image_rescue = any(c.path_name == 'image_enrichment_promoted' for c in candidates); if not _has_image_rescue and sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio: return VerdictResult('FAIL', ...)` — image enrichment explicitly bypasses structural hard-fail.
- **evaluate_gates** (verdict.py:119-217): `if sig.node_count == 0 or len(sig.flat_text.strip()) == 0: return GateOutcome(...hard_fail_verdict=VerdictResult('FAIL', 'zero_content'...))` — zero-content is hard-fail, but promotions can fire on near-zero content.

#### Key Files

- `src/pageindex_mcp/helpers/verdict.py`
- `src/pageindex_mcp/config.py`
- `src/pageindex_mcp/storage/verdict.py`

---

### Zone 3: OCR Pipeline Flag Conflation and Re-entry Hazards

**Severity:** critical | **Bug count:** 7

#### Mechanism

The OCR pipeline has three interacting structural defects involving cross-module implicit state coupling without type-system enforcement. The generative mechanism is that write and read sites are in unrelated modules, and re-entry guards are checked after branch conditions that short-circuit around them.

1. `decide_ocr_strategy` checks the UNIFIED_OCR_PLAN_ENABLED + document_type='image' branch BEFORE the full_page_already_applied re-entry guard, so for image documents the unified-plan branch always wins even after a full-page OCR pass has already run.

2. The legacy `decide_ocr_mode` function still exists as a thin wrapper with only 3 of 8 parameters, creating risk of callers using the reduced interface and missing document_type/ocr_langs discrimination.

3. `_execute_ocr_retry` implements a keep-best guardrail whose comparison logic makes OCR retry arithmetically impossible for no-text-layer PDFs — when pre-retry chars are zero, post_retry_chars < pre_retry.total_chars is always false (0 < 0), falling through to garble comparison where empty text returns empty prongs, setting retry_wins=True... but interaction with the density comparison at 0.80 threshold means a 69% content loss gets reverted every single retry attempt.

#### History

- **RFC-029 D4:** Keep-best guardrail made OCR retry arithmetically impossible for no-text-layer PDFs (69% loss reverted every time).
- **OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION P1:** Single `_OCR_ESCALATION` kill-switch gating both page-level and per-picture mechanisms; proposed split never landed.
- **Per-picture OCR:** Fires unconditionally inside `pdf_to_markdown_docling` and runs SECOND time during page-level `force_full_page_ocr` escalation (competing OCR passes).
- **RFC-025 D1:** `_text_layer_has_content` from header/footer text disabled picture OCR (503k→382 chars).
- **D0 page-coverage filter:** >60% coverage filter stops rescanning, but sub-60%-coverage charts still get re-OCR'd at 300 DPI, garbling numerals.

#### Code Evidence

- **decide_ocr_strategy** (picture_plane.py:357-423): UNIFIED_OCR_PLAN_ENABLED check at line ~389 returns OcrMode.FULL_PAGE before full_page_already_applied check at line ~397.
- **decide_ocr_mode** (picture_plane.py:430-448): Thin wrapper with only 3 params (ocr_escalation_enabled, has_image_markers, force_full_page), missing document_type/ocr_langs.
- **_execute_ocr_retry** (recovery.py:82-303): Keep-best at ~line 228: `if post_retry_chars < pre_retry.total_chars: retry_wins = False` — for equal chars falls to garble comparison; for post > pre, density comparison with `_density_improved = _post_density < _pre_density * 0.80`.
- **_text_layer_has_content** (pictures.py:267-299): Delegates to detect_garble, returns True (suppressing OCR) when text passes garble check regardless of header/footer-only status.

#### Key Files

- `src/pageindex_mcp/picture_plane.py`
- `src/pageindex_mcp/client/recovery.py`
- `src/pageindex_mcp/converters/pictures.py`
- `src/pageindex_mcp/config.py`

---

### Zone 4: Content-Destructive Heuristic Chains

**Severity:** high | **Bug count:** 6

#### Mechanism

Stripping and normalization heuristics added to clean markdown artifacts systematically produce catastrophic content loss on documents they were not designed for, and subsequent guards added to fix the content loss over-correct into opposite failure modes on yet other documents. The generative mechanism is unconstrained string-manipulation heuristics applied to unstructured markdown without bounds checking or document-class discrimination.

Each heuristic (fence stripping, ToC heading removal, picture splice) is designed to fix noise in one document category but operates on raw markdown string without understanding document structure. A parity-toggle (fence marker counting) turns a single stray character into a total-content-loss event because the toggle has no bounds. A depth guard added to prevent over-stripping then itself becomes an over-stripping heuristic because the guard's threshold cannot distinguish ToC headings from content headings.

#### History

- **RFC-034 D11:** ToC-heading stripping collapsed Penal Code from depth 3 to depth 2 (493/595 nodes flattened).
- **RFC-034 D16:** Guard for D11 over-stripped Federal Decree-Law 47 into 88% body-less heading fragments (MARGINAL→FAIL).
- **RFC-029 D3:** Fence/HR stripping caused 89-100% content loss in 5 docs (SLA 264→0 blocks, MOU 89% loss, Reitlehrer PASS→MARGINAL).
- **RFC-020:** Picture-splice removal caused 5 Arabic PDFs flat regression with 60% content loss.
- **RFC-029 D3:** Fence-marker parity toggle permanently silences content after stray backtick.

#### Code Evidence

The heuristics operate in the converters layer on raw markdown strings (converters/normalize.py, helpers/tree_split.py). RFC-029 D3's fence stripping and RFC-034 D11's ToC-heading stripping are both string-level operations without structural awareness. The parity-toggle bug (fence marker counting) has no bounds check — once a stray backtick flips the toggle, all subsequent content is treated as inside a code fence and stripped.

#### Key Files

- `src/pageindex_mcp/converters/normalize.py`
- `src/pageindex_mcp/helpers/tree_split.py`

---

### Zone 5: Verdict Persistence Competing Writers

**Severity:** high | **Bug count:** 5

#### Mechanism

The verdict persistence layer has a competing-writer pattern where the same MinIO key (processed/{doc_id}.meta.json) is written by two different processes: first a provisional write from the isolated converters_cli child subprocess via save_doc_meta, then an authoritative backfill write from the worker parent via _upsert_registry_row after Postgres CAS arbitration. The ordering is enforced only by async sequencing, not locking.

Three stores (MinIO sidecar, Postgres registry, Redis cache) each hold verdict state, written by different processes at different times with different guarantees. Postgres is designated the 'true arbiter' via CAS priority, but the MinIO sidecar is written first by the child process (which has no Postgres pool) and then overwritten by the parent. If the parent's backfill write fails (best-effort, non-fatal), the sidecar retains the child's provisional verdict which may disagree with Postgres.

#### History

- **RFC-036 D1:** Shrank `_WRITE_BARRIER_DELAYS` from 4.4s/8.8s to 0.45s and added `_verdict_cas_guard`, but Python-side and SQL-side CAS logic remained asymmetric.
- **Flat-doc path:** Still triple-writes, bypassing the consolidation.
- **Converters_cli boundary:** Identified as additional race surface not covered by CAS guard.
- **RFC-027 task 4.2:** `chunked_docling_timeout_s` created but never wired to worker.py (marked complete in tasks file).

#### Code Evidence

- **save_doc_meta** (storage/verdict.py:78-185): Sole authoritative entry point, invoked from both converters_cli child and worker parent. Comment at ~line 180: 'Zone-4 Phase 3: write-visibility barrier removed. Postgres is the sole verdict authority; the sidecar is archival-only'.
- **_upsert_registry_row** (registry_mirror.py:55-155): `winning = await upsert_doc(fields, force_verdict_override=_force_override); if winning: await asyncio.to_thread(save_doc_meta, doc_id, winning)` — backfill is best-effort inside try/except.
- **upsert_doc** (registry/queries.py:130-184): `sql = _UPSERT_OVERRIDE_SQL if force_verdict_override else _UPSERT_SQL` — two SQL variants, one with CAS guard, one without.

#### Key Files

- `src/pageindex_mcp/storage/verdict.py`
- `src/pageindex_mcp/worker/registry_mirror.py`
- `src/pageindex_mcp/registry/queries.py`
- `src/pageindex_mcp/client/indexer.py`

---

### Zone 6: Landscape/Rotation and Remote Route Divergence

**Severity:** high | **Bug count:** 5

#### Mechanism

The landscape detection and rotation correction pipeline has structural divergence between the local (docling) and remote (Scaleway) processing routes, and between the PDF metadata source and the content source. The generative mechanism is route-dependent code paths with no enforcement of feature parity.

RFC-026 D2 rotation correction applies only in the docling route, not the pymupdf4llm fallback route. Two landscape detectors use contradictory predicates (rotate % 180 != 0 or w>h vs rotate == 0 and w>h). The landscape probe reads the ORIGINAL PDF for orientation metadata while char counts come from the rotation-normalized temp copy — a metadata/data mismatch. RFC-035 D2's landscape serial loop over flagged pages is uncapped and blows the 1500s timeout. The remote Scaleway Docling service runs a stale image predating locally-implemented guards (RFC-033 D2's heading-order guard existed in no commit at all).

#### History

- **RFC-026 D2:** Rotation applying only in the docling route (not pymupdf4llm fallback).
- **Two landscape detectors:** With contradictory predicates.
- **RFC-035 D2:** Regressed landscape AND portrait uae_numbers variants together (Run 19).
- **Metadata/data mismatch:** Landscape probe reading original PDF while char counts come from rotation-normalized temp copy.
- **RFC-033 D2:** Heading-order guard existing in NO commit (git log -S finds nothing); worker never re-normalizes remote route markdown (23/23 headings corrupted).
- **RFC-032 D3/D9:** 3x worker timeout multiplier empirically insufficient (actual range 2.32x-11.00x), recalibrated to 16.5x.
- **RFC-028 D0:** `chunked_docling_timeout_s` never wired.

#### Code Evidence

- **decide_ocr_strategy** (picture_plane.py:357-423): document_type parameter discriminates pdf vs image routes, but rotation correction is route-dependent per the code map.
- **_upsert_registry_row** (registry_mirror.py:55-155): When registry_fields supplied by child process, MinIO re-read is skipped — but child may have run on remote route with stale image.
- **ensure_tessdata** (ocr_langs.py:91-188): Final fallback to `['deu', 'eng']` is route-independent, applying equally to local and remote paths.

#### Key Files

- `src/pageindex_mcp/converters/normalize.py`
- `src/pageindex_mcp/picture_plane.py`
- `src/pageindex_mcp/client/recovery.py`
- `src/pageindex_mcp/config.py`

---

### Zone 7: Image Block Conflation and Marker Survival

**Severity:** medium | **Bug count:** 4

#### Mechanism

The picture-splice pipeline has a structural conflation where per-picture OCR text is relocated from block['text'] (prose, visible to content_class) to block['ocr_text'] (image block, invisible to content_class), degrading retrieval granularity. The generative mechanism is an enrichment pipeline with incomplete state tracking.

The `_recover_picture_results` dense-fill fallback never distinguishes 'recovery attempted, found nothing' from 'never tried', so literal `<!-- image -->` markers survive verbatim into flat-doc output when neither ocr_text, desc, nor png_bytes exist. The `image_to_markdown()` path for standalone image files (.jpg/.png) never calls `_enrich_image_blocks` or `splice_figure_markers` at all, creating a complete bypass for an entire document type. The D0 page-coverage >60% filter stops full-page scanned regions from being re-OCR'd but lets sub-60%-coverage charts bypass the gate, garbling small-font numerals.

#### History

- **RFC-017/018 D0:** N duplicate PictureResults to satisfy splice_figure_markers marker-count guard, relocating OCR text from prose to image blocks (6+ regressions Run 6).
- **D0/D1 filters:** Unresolved `<!-- image -->` markers survive verbatim (GHV-TKV-Tarif: 3 of 4 markers survive as 42 chars noise).
- **Client.py C5:** `image_to_markdown()` path never calls `_enrich_image_blocks` or `splice_figure_markers` (pie-chart numeric labels completely lost).
- **D0 page-coverage filter:** >60% coverage filter stops rescanning, sub-60%-coverage charts garble small-font numerals ('20l9 2O2O 202l' spliced over correct '2019 2020 2021').

#### Code Evidence

- **decide_ocr_strategy** (picture_plane.py:357-423): Per-picture OCR mode returned when `ocr_escalation_enabled and has_image_markers` at line ~416.
- **_text_layer_has_content** (pictures.py:267-299): Returns `not detect_garble(text, script_context=_ctx, config=_garble_config)` — suppresses OCR on non-garbled text regardless of header/footer-only status.
- **Client.py image path:** `image_to_markdown()` path bypasses `_enrich_image_blocks` entirely; confirmed by separate document_type='image' branch in decide_ocr_strategy that only fires when UNIFIED_OCR_PLAN_ENABLED.

#### Key Files

- `src/pageindex_mcp/picture_plane.py`
- `src/pageindex_mcp/converters/pictures.py`
- `src/pageindex_mcp/client/indexer.py`

---

### Zone 8: Verified-Locally-Never-Deployed Fix Drift

**Severity:** medium | **Bug count:** 4

#### Mechanism

The generative mechanism is a documentation/deployment gap where the task-tracking system (tasks files, RFC deliverable markers) is disconnected from the actual deployment pipeline (git commits, container image builds, service deployments). A fix can be 'complete' in the task file while existing only in the working tree, never committed, and the remote service runs a stale image with no automated parity check against the local codebase.

This creates a state where the documented system and the running system diverge silently — subsequent RFCs are written against the documented (fixed) state, so they cannot anticipate the still-present defect in production. The 0%-TPR promotion pattern is a variant: a detector's null output is treated as evidence of safety rather than evidence of detector failure.

#### History

- **RFC-033 D2:** Heading-order guard `_heading_is_logical_order` exists in NO commit (git log -S finds nothing); worker never re-normalizes remote route markdown (23/23 headings corrupted on fresh Arabic document ingest).
- **RFC-027 task 4.2:** `chunked_docling_timeout_s` created but never wired to worker.py, marked complete in tasks file; world-stats-pocketbook timed out 3 consecutive runs (ERROR, FAIL, ERROR) before RFC-032 D3/D9 recalibrated multiplier.
- **RFC-033 D2:** `_check_bidi_coherence` detector promoted to BIDI_COHERENCE_ENFORCE=true default on a 0%-TPR instrument, misreading zero detections as zero violations.

#### Code Evidence

- **ensure_tessdata** (ocr_langs.py:91-188): Documents the TessdataUnavailableError for non-Latin languages as a Zone-3 fix, but final fallback to `['deu', 'eng']` at lines 186-188 still fires when no languages are available — the fix for non-Latin languages (raising error) is bypassed by catch-all fallback.
- **_upsert_registry_row** (registry_mirror.py:55-155): The skip when `get_pool() is None` queues a retry via `_enqueue_verdict_retry`, but this path was added in Zone-4 Phase 3 — prior to that, pool unavailability silently dropped the write.

#### Key Files

- `src/pageindex_mcp/converters/normalize.py`
- `src/pageindex_mcp/worker/registry_mirror.py`
- `src/pageindex_mcp/converters/ocr_langs.py`

---

## Cross-Cutting Themes

1. **Interface-level fixes without ordering/arithmetic fixes:** Repeatedly, a fix consolidates the *interface* (typed dataclasses like GarbleProfile/RecoveryOutcome, single entry points like compute_verdict/check_garble, wiring cleanups) while leaving the underlying ordering or arithmetic defect in place one layer deeper — NFKC destroying Presentation Forms before garble/bidi detectors ever run, RFC-029 D4's keep-best guardrail making retry arithmetically impossible, GATE_TABLE's positional coupling surviving the dual-engine consolidation.

2. **Verified-locally-but-never-committed/deployed fixes:** Multiple critical fixes (RFC-033 D2's heading guard, RFC-029 D6's judge calibration rules, RFC-027's chunked_docling_timeout_s wiring) were implemented, tested, and marked complete in tasks files, but never actually landed in a commit or reached the deployed artifact (notably the stale Scaleway Docling image), so the documented state and the running state diverge silently.

3. **Two independent, parallel verdict/gate engines that can disagree:** validate_tree's gate-table cascade and classify_verdict's grouped-rule engine compute verdicts independently and inconsistently across many RFC generations; consolidation attempts (compute_verdict, REASON_POLICY, HARD_FAIL_DEFECTS) repeatedly land as unwired infrastructure rather than replacing the duplicate logic, and a flat-doc routing path bypasses the gate table entirely in violation of Hard Rule 5.

4. **Duplicated logic drifting apart across two call sites:** The same check (digit-ratio garble floor, _OCR_ESCALATION constant, garble logic across _tree_is_garbled/_flat_text_is_garbled) exists in two places; fixes land in one copy and not the other ('fix-one-miss-the-other drift'), and this exact pattern is independently rediscovered across at least 3 RFC generations (RFC-010, RFC-013, ISS-36) without being permanently closed.

5. **Threshold ratcheting as symptom management:** PASS_MAX_LEAF_RATIO and similar quality thresholds get repeatedly widened across RFCs (0.17→0.20→0.30) to suppress false-positive FAILs, each widening documented with its own predicted risk that then materializes, while the underlying structural-ambiguity root cause in the verdict engine is never addressed — hysteresis mechanisms meant to dampen flip-flopping are themselves defeated by unrelated operational events (corpus reingestion wiping meta.json sidecars).

6. **Fix-for-a-fix chains producing the opposite failure mode:** A stripping/normalization heuristic added to fix one document's over-inclusion of noise (RFC-034 D11 ToC stripping, RFC-029 D3 fence stripping) causes catastrophic content loss on other documents, and the subsequent guard added to fix THAT (RFC-034 D16) over-corrects into a different failure mode (body-less heading fragments) on yet another document — the defect class migrates rather than closing.

7. **Shared kill-switches gating conceptually independent mechanisms:** A single env-var/flag (e.g. _OCR_ESCALATION) controls two logically distinct subsystems (page-level OCR escalation vs. per-picture enrichment), so a fix or config change targeting one silently affects the other; proposed splits into separate flags are repeatedly identified but never landed.

8. **Detection instruments misread as clean bills of health:** A detector measuring 0% (zero violations / zero true positives) is treated as evidence of safety and used to justify promoting a stricter default (BIDI_COHERENCE_ENFORCE=true), when the 0% actually reflects the detector being structurally incapable of firing at all (null-detector fallacy) — the same misreading pattern recurs with RFC-033 D2's instrument and separately with 'zero regressions' claims on threshold widenings that had already-known misses.

9. **Script/language-detection blind spots recurring across the Arabic/RTL pipeline:** expected_script self-inferred from already-corrupted text, Latin-gibberish-in-Arabic-context undetected, ensure_tessdata silently substituting deu/eng for unavailable Arabic tessdata, and NFKC destroying presentation-form signals collectively produce a persistent class of Arabic documents that pass verdict gates while containing garbled/reversed/mistranslated text — targeted by 5+ RFCs (010, 015, 018, 026, 027, 028, 033) without the class ever fully closing.

10. **Cross-process/cross-store consistency races in verdict persistence:** Multiple writers (registry, MinIO meta sidecar, cache) to verdict state create write-visibility races; fixes (CAS guards, shrunk write-barrier delays) address the common-case path but leave asymmetric Python/SQL logic, uncovered subprocess boundaries (converters_cli), and an entirely un-consolidated flat-doc triple-write path.

11. **Right-to-erasure cascade gaps mirroring the extraction pipeline's own fragmentation:** delete_doc's fire-and-forget registry delete (no await, no timeout) and its failure to remove preloaded/<filename> both violate Hard Rule 2's mandated cascade order, following the same 'each step can fail independently, no rollback' pattern seen in the extraction/verdict pipelines.

12. **Detection without remediation:** Garble gate fires but OCR/VLM recovery not wired; verdict detection fires but promotion bypass allows PASS anyway.

---

## Key Observations

- **Cascading violations of Hard Rules 2 and 5:** Zone 2 (verdict bypass), Zone 5 (competing writers), and Zone 11 (erasure cascade) all directly violate Hard Rule 2 (right-to-erasure cascade) and Hard Rule 5 (never silently persist low-quality trees).

- **Ordering-dependent signal destruction:** Zones 1, 3, 6, and 4 all feature mechanisms where signals are destroyed before detection instruments can inspect them (NFKC before garble detectors, OCR branches checked before re-entry guards, stripping heuristics without bounds).

- **Common-cause fix-and-miss drift:** Zones 1, 3, 4, and 8 all feature the same root cause: a fix lands in one location and is missed in another (two garble checks, two OCR paths, header/footer stripping with depth guards).

- **Local/remote divergence:** Zone 6 specifically features code paths that are functionally different between local (docling) and remote (Scaleway) execution, with no automated parity check.

- **49 bugs attributed across 8 zones** with 10 critical/high-severity bugs in the garble, verdict, and OCR subsystems.

---

**Report generated:** 2026-08-26  
**Report version:** POST-FIX-12  
**Audit scope:** Architecture defect zones audit, evidence-driven zone specification

---

## Simplification Proposals

### Garble Detection Surface Fragmentation

1. Core simplification: Move all garble/bidi/script detection to run on raw codepoints BEFORE NFKC canonicalization, and stop deriving expected_script from the document's own text -- pass it down explicitly from an upstream, pre-corruption source (declared document language / a separate clean-sample pass) or mark it 'unknown' rather than self-infer. Collapse the four independent garble prongs (digit_ratio, latin_gibberish, presentation-forms, bidi_coherence) into one ordered pipeline function so a fix to one prong can't be silently defeated by ordering elsewhere. 2. Concrete steps: (a) garble.py -- extract detect_garble's prong list (lines ~318-405, ~494-564) into a single `_run_garble_prongs(raw_text, expected_script)` called with pre-normalization text; delete the self-inference fallback `_infer_script(blob)` and require expected_script as a mandatory parameter (~ -40/+20 lines). (b) normalize.py / pictures.py -- reorder `_pre_inference_normalize` (normalize.py:129-161, pictures.py:133-171) so garble detection happens before the `unicodedata.normalize('NFKC', ...)` call at line 157/167; keep the had_pres_forms boolean but make it the actual detection point, not a pre-capture that's then destroyed (~10 line delta, reorder not rewrite). (c) script.py -- add an explicit expected_script parameter threaded from the document's declared/config language into detect_garble call sites (~15 lines). (d) ocr_langs.py -- replace the blind `return ['deu','eng']` fallback (186-188) with a fallback keyed off the same expected_script value used by garble detection, so OCR language selection and garble detection agree (~10 lines). Net: consolidation and reordering, roughly -30 to +40 net lines, no new abstraction layer. 3. Historical bug classes prevented: RFC-028 D2 presentation-forms prong defeated by NFKC ordering; RFC-033 D2 bidi_coherence 0%-true-positive measurement error; BIDI_COHERENCE_ENFORCE promoted to default-true on a false clean bill of health; any future 'add a new prong' fix that gets silently neutralized by normalization order. 4. Migration risk: reordering normalization changes what text downstream converters see -- must re-run corpus regression (corpus-regression-watchdog) before merge; the self-inference removal is the higher-risk change since some call sites may not have expected_script available yet, requiring a temporary 'unknown -> skip script-specific prongs, keep script-agnostic ones' fallback rather than a hard failure. Sequence: (1) add explicit expected_script parameter with `unknown` default that preserves current behavior, (2) reorder detection before NFKC behind a feature flag, (3) validate against corpus with flag on, (4) remove self-inference and flip flag default, (5) delete OCR fallback divergence last since it's lowest-risk. 5. Effort: 3-4 days (1 day reorder + prong consolidation, 1 day expected_script threading, 1 day OCR fallback alignment, 1 day corpus validation).

### Verdict Gate Promotion Bypass Cascade

1. Core simplification: Delete the priority-max `apply_promotions` candidate system and replace it with a single ordered decision list evaluated top-to-bottom where a hard-fail check (zero_content, max_leaf_ratio) is evaluated FIRST and unconditionally, with no candidate able to outrank it structurally -- promotions become narrowing exceptions checked only after hard-fail clears, not competing priorities. This removes the class of bug where a new rescue path (image_enrichment_promoted) is given a numeric priority that happens to beat structural safety. 2. Concrete steps: (a) verdict.py -- collapse `evaluate_gates` (119-217) and `apply_promotions` (407-518) into one function `compute_verdict_ordered()` that runs hard-fail checks first, unconditionally, with no early-return path from promotions above them; remove the priority/`max(candidates, key=lambda c: c.priority)` mechanism entirely and replace image_enrichment_promoted with an explicit guard clause: only consider it when node_count > 0 AND flat_text length exceeds a minimum floor (not a promotion that can override zero/near-zero content) (~-60/+40 lines, net simplification). (b) config.py -- consolidate the threshold history (0.17->0.20->0.30) into one named constant with a single source of truth and a changelog comment instead of scattered literals (~10 lines). (c) storage/verdict.py -- decouple hysteresis/anchoring from meta.json sidecar existence; if the anchor state is missing, treat as 'no anchor' (fresh evaluation) rather than deriving a default from a destroyed value -- add an explicit null-check with logging (~15 lines). 3. Historical bug classes prevented: 2-3 block / 38-123 char documents getting PASS via image_enrichment_promoted outranking structural hard-fail; RFC-024's self-predicted RFC-025 threshold-widening failure; hysteresis destabilization after corpus reingestion wipes meta.json anchors. 4. Migration risk: this changes verdict outcomes for the exact documents currently rescued by promotion paths -- must run against the full labeled corpus and diff PASS/FAIL counts before merge (corpus-diff / corpus-score-diff skills exist for this). Sequence: (1) add the unconditional hard-fail-first ordering behind a flag, run corpus diff to quantify newly-FAILing docs, (2) manually review those docs to confirm they were false PASSes, (3) tune the minimum floor for image_enrichment's remaining exception path using that review, (4) flip default, (5) remove dead priority-scoring code last. 5. Effort: 4-5 days (2 days restructuring + guard clause, 1 day hysteresis null-check, 1-2 days corpus diff review and threshold tuning).

### OCR Pipeline Flag Conflation and Re-entry Hazards

1. Core simplification: Replace the implicit cross-module boolean flag (state.full_page_already_applied, written in recovery.py and read in picture_plane.py) with a single explicit OcrDecision return value threaded as a normal function argument/return, and move the UNIFIED_OCR_PLAN_ENABLED short-circuit to run AFTER (not before) the re-entry guard so unification can't skip it. This removes the class of bug where two unrelated modules communicate through mutable shared state instead of a call/return contract. 2. Concrete steps: (a) picture_plane.py -- in `decide_ocr_strategy` (357-423), reorder so the `full_page_already_applied` check happens before the UNIFIED_OCR_PLAN_ENABLED branch (389-396) rather than after (397), and change decide_ocr_mode (430-448) to accept the full decision context (document_type, ocr_langs) instead of the current 3-param thin wrapper so future flags can't be silently dropped (~20 lines net, mostly reordering). (b) client/recovery.py -- change `_execute_ocr_retry` (82-303) to return the already-applied state as part of its result object instead of mutating `state.full_page_already_applied` as a side effect at line ~186; callers explicitly pass that forward (~15 lines). (c) picture_plane.py / config.py -- keep OCR_ESCALATION_GARBLE and OCR_ESCALATION_PER_PICTURE split (already done) but add a single doc-comment plus an assertion that they are read independently, preventing future re-unification regressions (~5 lines). (d) recovery.py -- fix the keep-best arithmetic (~line 228) so equal-or-improved character count with no-text-layer source documents doesn't fall through the density-improved 0.80 threshold that structurally can't be met when starting from zero text (~10 lines, add an explicit 'no prior text layer' branch that always accepts the retry). 3. Historical bug classes prevented: UNIFIED_OCR_PLAN_ENABLED short-circuit bypassing the re-entry guard causing duplicate full-page OCR; per-picture/page-level kill-switches accidentally coupled; no-text-layer PDFs (the primary OCR-recovery target) failing the keep-best guardrail and never getting retry benefit. 4. Migration risk: moderate -- the keep-best arithmetic change directly affects which OCR result gets persisted, so needs before/after comparison on documents with no original text layer specifically (a corpus subset, not the whole corpus). Sequence: (1) fix the ordering bug (guard before unified short-circuit) first since it's the highest-confidence, lowest-risk change, (2) convert the flag to explicit return/pass-through, (3) fix keep-best arithmetic last with targeted no-text-layer corpus validation. 5. Effort: 2-3 days (1 day ordering + flag-to-return refactor, 1 day keep-best fix, 0.5-1 day targeted validation).

### Content-Destructive Heuristic Chains

1. Core simplification: Replace the unbounded string-level parity-toggle fence-stripping heuristic with a bounds-checked, structurally-aware pass that only strips a fence when both an opening and matching closing marker are found within the same block (never a running toggle across the whole document), and cap the ToC-heading-removal depth guard's blast radius by requiring it to leave a minimum body-content floor per node rather than a static depth threshold alone. 2. Concrete steps: (a) converters/normalize.py -- rewrite the fence-stripping logic to use a proper open/close marker match (regex or line-scan with immediate rollback if no matching close is found before EOF) instead of a running parity counter that has no bounds check; a stray backtick then affects only its own unmatched span, not the rest of the document (~-30/+25 lines). (b) helpers/tree_split.py -- change the RFC-034 D16 depth guard from a static-threshold-only check to threshold-plus-minimum-body-length, so a heading that would be stripped to a body-less fragment is instead left as content (guards against both over-stripping failure modes with one condition instead of tuning a single knob back and forth) (~15 lines). 3. Historical bug classes prevented: total-content-loss from a single stray backtick flipping a global parity toggle (RFC-029 D3 fence-stripping); the fix-for-a-fix cycle where the depth guard (meant to fix ToC over-stripping) produced body-less fragments (RFC-034 D11/D16 regression pair). 4. Migration risk: low-to-moderate -- these are narrowly-scoped string-heuristic fixes, but must be validated against documents known to trigger both directions of the historical bug (docs with legitimate nested code fences, and docs with deep ToC headings) via corpus-diff. Sequence: (1) fix fence-stripping bounds check first (isolated, low blast radius), (2) validate against fence-heavy corpus subset, (3) add body-length floor to depth guard, (4) validate against ToC-heavy corpus subset. 5. Effort: 1.5-2 days (0.5 day fence fix, 0.5 day depth-guard fix, 0.5-1 day corpus validation on both subsets).

### Verdict Persistence Competing Writers

1. Core simplification: Eliminate the dual-writer race by making the child process (converters_cli) stop writing the MinIO sidecar directly and instead write only a provisional/local result that the parent (worker, which owns the Postgres pool) picks up and persists as the single write path -- collapse 'sidecar written twice by two processes' into 'sidecar written once by the process that also owns the CAS-guarded SQL write.' The force_verdict_override escape hatch should apply symmetrically to both stores or be removed from the SQL layer, not exist only there. 2. Concrete steps: (a) storage/verdict.py -- change `save_doc_meta` call sites so the child process (_persist_tree_result/_persist_flat_result) writes to a temp/local location or an unpersisted-marker sidecar key, not the final MinIO sidecar path; only the parent's backfill path writes the final sidecar, after Postgres CAS succeeds (~20 lines, mostly call-site changes). (b) worker/registry_mirror.py -- in `_upsert_registry_row` (55-155), make the sidecar write (`save_doc_meta`) NOT best-effort/non-fatal -- if it fails after a successful Postgres CAS win, retry with backoff or raise so the job surfaces as failed rather than silently leaving stores disagreeing (~15 lines). (c) registry/queries.py -- either extend `force_verdict_override` to also gate a corresponding sidecar overwrite explicitly (so the escape hatch is symmetric across stores), or restrict force_verdict_override to an explicit admin-only path with a single call site that updates both stores atomically-in-sequence with verification (~20 lines). 3. Historical bug classes prevented: child-writes-provisional-then-parent-backfill-fails-silently leaving sidecar/Postgres disagreement; force_verdict_override bypassing CAS in SQL while sidecar has no equivalent guard, producing store divergence. 4. Migration risk: moderate-to-high -- this touches the write path for every ingested document, and the child process currently writes without a Postgres pool by design (per architecture), so removing its direct sidecar write requires confirming no other consumer reads the sidecar before the parent backfill completes (check for any status-polling code that reads the child's provisional sidecar). Sequence: (1) instrument to detect actual sidecar/Postgres disagreement frequency in current corpus before changing anything (audit-reconcile skill), (2) make backfill failure non-silent (loud, retried) as the lowest-risk first step, (3) move child to write provisional/non-final sidecar location, (4) symmetric-gate or restrict force_verdict_override last since it's the escape hatch used operationally. 5. Effort: 3-4 days (0.5 day instrumentation, 1 day backfill-failure hardening, 1-1.5 days child-write relocation, 0.5-1 day override symmetry + validation).