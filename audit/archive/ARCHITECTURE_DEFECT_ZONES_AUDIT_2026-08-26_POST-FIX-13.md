# Architecture Defect Zones Audit — 2026-08-26 POST-FIX-13

**Date:** 2026-08-26  
**Run:** POST-FIX-13

## Summary Table

| # | Zone | Severity | Bug Count | Key Files |
|---|------|----------|-----------|-----------|
| 1 | OCR Pipeline Filter Composition and Re-entry Hazards | critical | 15 | picture_plane.py, pictures.py, recovery.py, indexer.py |
| 2 | Garble Detection Surface Fragmentation | critical | 12 | garble.py, script.py, gates.py, tree_validation.py |
| 3 | Verdict Gate Promotion Bypass Cascade | critical | 11 | verdict.py, tree_validation.py, gates.py, config.py |
| 4 | Pre-Tree Text Transform Table Fracture | high | 8 | tree_split.py, headings.py, indexer.py |
| 5 | Verdict Persistence Competing Writers | high | 7 | verdict.py, registry_mirror.py, reconcile.py, queries.py |
| 6 | Gate-to-Recovery Signal Threading Gaps | high | 6 | gates.py, tree_validation.py, recovery.py, indexer.py |
| 7 | Remote vs. Local Execution Divergence | medium | 5 | config.py, recovery.py, headings.py, subprocess_mgr.py |

---

## Zone Details

### Zone 1: OCR Pipeline Filter Composition and Re-entry Hazards

**Severity:** critical | **Bug count:** 15

#### Mechanism

The generative mechanism is **ORDER-DEPENDENT FLAG INTERACTION** across multiple decision sites. `decide_ocr_strategy` (picture_plane.py:357-430) sequences five branches: re-entry guard, UNIFIED_OCR_PLAN_ENABLED image-doc branch, force_full_page, per-picture escalation, and NONE fallback. Each branch short-circuits before later ones, so adding or modifying any branch changes which documents reach downstream branches. The UNIFIED_OCR_PLAN_ENABLED branch explicitly runs AFTER the re-entry guard (a Zone-2 fix for a concrete prior bug where image docs bypassed the guard), but callers in recovery.py set full_page_already_applied in one code path and read it in another (picture_plane.py), creating cross-module state coupling.

Meanwhile, `_text_layer_has_content` (pictures.py:267-299) gates per-picture OCR with a char-count floor AND a garble check, but the page-level text-layer check upstream can short-circuit before clip_text extraction runs — a concrete bug that recurred from RFC-018 through RFC-025.

The marker-count-duplication workaround (client.py creates N duplicate PictureResults to satisfy splice_figure_markers's count guard) adds another interacting constraint: adjusting picture classification or OCR routing changes the marker count, which changes whether the splice guard passes, which changes enrichment results.

#### History

- **Chain 1 (RFC-018→019→020→021/022→024/025):** RFC-018 D0 added a page-coverage exemption filter, D3a added forced OCR for scanned pages. Together they reclassified PictureItems as TextItems (0 PictureResults), stripped heading structure, and forced flat routing for docs 7,17,20,21. RFC-019 D0/D1 combined with this filter to zero out all enrichment on docs 3 and 9 (doc 3: 1/4→0/3, doc 9: 3→0). This required five further fixes (RFC-020 F1-F5) to the same filter composition, then RFC-021 QF1 → RFC-022 B3 caused GHV-TKV OCR splice regression, then RFC-024 D1 → RFC-025 D1 found clip_text was never executed (Human Rights doc 503k→382 chars).

- **Chain 12:** UNIFIED_OCR_PLAN_ENABLED branch explicitly short-circuits before the full_page_already_applied re-entry guard is checked, creating a confirmed exploitable OCR re-entry path — zone severity escalated high→critical between 08-24 and 08-26 audits.

- **Chain 14:** RFC-018 D0 marker-count-duplication workaround causes unconditional per-picture OCR on every duplicated marker.

- **Chain 15:** OCR_IMAGE_BLOCK_CONFLATION investigation D0/D1 added text-layer probe, but clip_text was never executed on some documents until RFC-025 D1.

#### Code Evidence

- `decide_ocr_strategy` (picture_plane.py:357-430): re-entry guard at line 389 returns `OcrDecision(mode=NONE)` when `full_page_already_applied=True`. UNIFIED_OCR_PLAN_ENABLED branch at line 403 runs AFTER the guard — but the comment at line 399-402 explicitly documents this as a Zone-2 fix for a prior ordering bug where image docs bypassed the guard. UNIFIED_OCR_PLAN_ENABLED defined at picture_plane.py:350 as os.getenv default 'false'.

- `_text_layer_has_content` (pictures.py:267-299): two-stage gate — char-count floor (len <= _PICTURE_OCR_MIN_CHARS → False), then unconditional detect_garble call ('always on', D0 RFC-023).

- `decide_ocr_mode` (picture_plane.py:438-456): still exists as a legacy wrapper delegating to decide_ocr_strategy, confirming dual-site decision pattern was only partially consolidated.

- `RecoveryMixin._execute_ocr_retry` (recovery.py:83-316): sets full_page_already_applied at line 178 and reads it at line 107.

#### Key Files

- src/pageindex_mcp/picture_plane.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/client/indexer.py

---

### Zone 2: Garble Detection Surface Fragmentation

**Severity:** critical | **Bug count:** 12

#### Mechanism

The generative mechanism is **HEURISTIC INTERACTION WITH NORMALIZATION DESTRUCTION**. `detect_garble` (garble.py:494-564) normalizes text via `normalize_for_garble` before passing to `garble_prongs`, but NFKC normalization decomposes Arabic Presentation-Form codepoints (U+FB50-FEFF) into logical Arabic, destroying the very signal the presentation_forms prong keys on.

`ScriptContext.from_document` (script.py:896-968) computes `had_presentation_forms` from raw text pre-NFKC, and `detect_garble` at line 540 reads `script_context.had_presentation_forms`, with a fallback computation at lines 541-543 scanning the blob directly — but if the blob has already been NFKC-normalized before reaching `detect_garble`, the fallback always returns False.

`garble_prongs` (garble.py:318-405) has a second structural problem: multiple prongs have independent blind spots that interact. The digit_ratio prong (line 383) only fires when len(norm) > cfg.garble_digit_floor (default 500), so short garbled text passes uninspected. The latin_gibberish prong (line 392) requires garble_latin_gibberish_enabled AND expected_script must be available — but _script_from_filename returns None for German filenames, making the prong permanently unfireable for German docs.

The short_text_prior_garble short-circuit (lines 524-534) makes detect_garble non-idempotent: when blob_kind==RAW_MARKDOWN, original_defect was GARBLING/NODE_GARBLING, and text<200 chars, it forces is_garbled=True without running any heuristic, so a prior garbling verdict permanently poisons short post-retry text.

#### History

- **Chain 5 (RFC-013→018→019→025→028→029→030→033→034):** NFKC destroying presentation-form signal independently rediscovered in RFC-028 D2, RFC-033 D2, and RFC-034 D7. expected_script never threaded to garble callers (RFC-019 D2). node_garbling never recognized by OCR-escalation conditional (RFC-018 D3b).

- **Chain 7 (RFC-029→030→BIDI_ROOT_CAUSE_RFC033):** _check_bidi_coherence had 0% TPR because its only signal (Arabic Presentation-Forms unicodedata name substrings) can never appear in text sampled by its line-selector (U+0600-06FF excludes U+FB50-FEFF). Yet BIDI_COHERENCE_ENFORCE was promoted to default-true on the reasoning that zero violations meant zero risk rather than that the detector could not fire.

- **Chain 21:** D8 mixed-script regex included space in character class, flagging all legitimate Arabic prose.

- **Chain 22:** Markdown formatting dilutes digit-ratio below 60% threshold.

- **Chain 24:** _script_from_filename returns None for German, making latin_gibberish prong unfireable.

#### Code Evidence

- `detect_garble` (garble.py:494-564): short-circuit at lines 524-534 checks `blob_kind==RAW_MARKDOWN`, `config.garble_short_text_default`, `len(blob)<200`, `original_defect in (GARBLING, NODE_GARBLING)` and returns `GarbleReport(is_garbled=True, fired_prongs={'short_text_prior_garble'})` without calling `garble_prongs`.

- `ScriptContext` (script.py:869-968): docstring at line 881 states 'Post-NFKC the ratio is always 0 because presentation-form codepoints are decomposed into logical Arabic.' from_document at line 907 computes had_pf by scanning raw_text for PRESENTATION_RANGES codepoints, confirming pre-NFKC capture is intentional.

- `garble_prongs` (garble.py:318-405): digit_ratio at line 383 gated behind 'len(norm) > cfg.garble_digit_floor'; latin_gibberish at line 392 gated behind 'cfg.garble_latin_gibberish_enabled'; had_presentation_forms at line 368 simply adds the prong if True.

- `normalize_for_garble` (script.py:677-690): RAW_MARKDOWN path strips heading markers and table pipes but does NOT strip NFKC-decomposed presentation forms.

#### Key Files

- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/script.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/helpers/tree_validation.py

---

### Zone 3: Verdict Gate Promotion Bypass Cascade

**Severity:** critical | **Bug count:** 11

#### Mechanism

The generative mechanism is **MULTIPLE INDEPENDENT BYPASS PATHS WITH CIRCULAR THRESHOLD COUPLING**. `apply_promotions` (verdict.py:407-518) runs only when no HARD_FAIL fired, but the hard-fail check itself is conditionally gated: the max_leaf_ratio structural hard-fail at line 476 is evaluated 'if not _has_image_rescue' — so a fired image_enrichment_promoted candidate (priority=100, _try_image_enrichment at verdict.py:220-270) bypasses what would otherwise be an unconditional FAIL.

`_try_image_enrichment` checks image_enrichment_ratio >= 0.8 but has no minimum char floor for the PASS verdict path when total_chars >= th.min_image_promoted_chars — and below that floor, it returns a MARGINAL verdict still at priority=100, still outranking structural passes.

The threshold coupling is circular: widening PASS_MAX_LEAF_RATIO to reduce false FAILs (RFC-023 D10: 0.17→0.20, RFC-024 D0: 0.20→0.30) let garbled documents through; adding hysteresis (RFC-025 D0) to stabilize verdicts was defeated by reingestion wiping processed/*.meta.json (RFC-026 D3); and the hysteresis itself interacts badly with garble detection because it relaxes max_leaf_ratio when prior_verdict=='PASS', letting identical garbling metrics pass on re-score.

#### History

- **Chain 2 (RFC-023→024→025→026):** PASS_MAX_LEAF_RATIO widened three times, hysteresis added then defeated by ledger wipe. Run 10-12 verdict inflation/oscillation on unchanged extraction metrics.

- **Chain 11:** image_enrichment_promoted evolved from implicit drift into explicitly hard-coded priority=100 escape hatch. Bug count fell 10→8 but severity escalated high→critical because the bypass is now deliberate.

- **Chain 20:** RFC-024 threshold widening caused 'Haftpflicht' with 81/132 garbled nodes to flip FAIL→PASS, breaking verdict classification tests for ratio=0.20-0.21.

- **Chain 23:** Near-zero-content documents (marsoom 13/2022: 2 blocks/38 chars, al-qarar: 2 blocks/123 chars) earn PASS via promotion flag with no content-validity check.

- **Chain 27:** Hysteresis relaxes max_leaf_ratio for garbled trees when prior PASS exists.

- **Chain 26:** Reordering check in validate_tree but not in classify_verdict allowed reordered trees to PASS via verdict recompute path.

#### Code Evidence

- `apply_promotions` (verdict.py:407-518): hard-fail check at line 476 'if not _has_image_rescue and sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio' — the _has_image_rescue guard is computed at line 473 'any(c.path_name == "image_enrichment_promoted" for c in candidates)'.

- `_try_image_enrichment` (verdict.py:220-270): returns `PromotionCandidate(priority=100, path_name='image_enrichment_promoted', verdict='PASS')` at line 267 when ratio>=0.8 and total_chars >= min_image_promoted_chars and not garbled. Returns MARGINAL at priority=100 when below char floor (line 244).

- `GATE_TABLE` (gates.py:321-408): 10 active gates evaluated exhaustively, severity-ordered (GARBLING=0 highest, SUSPECT_DENSITY=9 lowest).

- `validate_tree` (tree_validation.py:262-354): returns only the first firing gate as primary defect even though all_defects carries every co-firing gate — callers reading the 2-tuple form lose co-firing information.

#### Key Files

- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/config.py

---

### Zone 4: Pre-Tree Text Transform Table Fracture

**Severity:** high | **Bug count:** 8

#### Mechanism

The generative mechanism is **PARALLEL LINE-LEVEL TRANSFORMS WITHOUT SHARED TABLE BOUNDARIES**. `headings.py` imports `compute_table_spans` and `line_in_table_span` from `tree_split.py` and calls them in all three heading injectors (confirmed: _inject_arabic_structural_headings at headings.py:146, _inject_german_clause_headings at headings.py:232, _inject_english_article_headings at headings.py:264).

However, `split_oversized_leaf_nodes` (tree_split.py:401-477) does NOT call `compute_table_spans` or `line_in_table_span` anywhere in its outbound call graph. The line_in_table_span function exists at tree_split.py:512-513 in the SAME file but is only referenced by headings.py. This means any pipe-table row that happens to match an ordinal pattern, ATX heading pattern, paragraph marker, or blank-line boundary gets split mid-row, fracturing the table across child nodes.

_segment_table_nodes and _repair_docling_tables handle table detection at the node level but operate AFTER the line-level splitters have already fractured raw text.

#### History

- **Chain 4 (RFC-005→010→028→029→033→034→035→036):** RFC-005 Fix-1 introduced split_oversized_leaf_nodes with no table guard. RFC-010 D4 collapsed marsoom 33 node_count 125→58. RFC-028 D1 heading injection blocked richer flat fallback, losing ~80% of marsoom 13's content. RFC-029 D4 _repair_docling_tables destroyed Schedule 1-5 in cabinet_resolution_no_21. RFC-033 D11 _strip_toc_heading_nodes over-stripped Penal Code depth 3→2. RFC-034 D16/D20 hit the same unguarded mechanism causing marsoom 13 depth 4→2. RFC-035 D2 shattered landscape chart axis labels into 71+ singleton kv blocks. Each fix touched a different transform in the chain but left the shared table-fracture surface intact.

#### Code Evidence

- `split_oversized_leaf_nodes` (tree_split.py:401-477): processes each leaf node's text, calling `_fold_with_index_map`, `_OVERSIZED_ORDINAL_RE.finditer`, `_split_on_atx_headings`, `_split_on_generic_numbered_lines`, `_split_on_paragraph_markers`, `_split_on_blank_line_paragraphs` — no call to `compute_table_spans` or `line_in_table_span` anywhere.

- `line_in_table_span` (tree_split.py:512-513): 'def line_in_table_span(idx: int, spans: list[tuple[int, int]]) -> bool: return any(lo <= idx < hi for lo, hi in spans)' — exists in the same file but is NOT called by the splitter.

- `_inject_arabic_structural_headings` (headings.py:102-204): computes 'table_spans = compute_table_spans(lines)' at line 144, then checks 'if line_in_table_span(i, table_spans)' at line 146 to skip table rows. Confirmed: headings.py imports both functions at line 10.

#### Key Files

- src/pageindex_mcp/helpers/tree_split.py
- src/pageindex_mcp/converters/headings.py
- src/pageindex_mcp/client/indexer.py

---

### Zone 5: Verdict Persistence Competing Writers

**Severity:** high | **Bug count:** 7

#### Mechanism

The generative mechanism is **DUAL-STORE EVENTUAL CONSISTENCY WITH ASYMMETRIC CAS GUARDS**. `_upsert_registry_row` (registry_mirror.py:55-155) is the Postgres-authoritative path: it CAS-upserts to Postgres (with RETURNING), then best-effort backfills the MinIO sidecar via save_doc_meta. But `save_doc_meta` (storage/verdict.py:78-185) is a read-merge-write that has no CAS guard — if the Postgres CAS accepted a higher-priority verdict but the sidecar backfill fails (exception caught at registry_mirror.py:144-149), the sidecar retains a stale verdict until reconcile_registry_drift heals it.

When the Postgres pool is unavailable, `_upsert_registry_row` at line 99 queues a Redis retry via `_enqueue_verdict_retry`, but if `_enqueue_verdict_retry` itself throws, the failure is swallowed.

The reingestion pipeline wipes processed/*.meta.json, destroying the hysteresis ledger that find_prior_verdict scans, so verdicts computed with hysteresis context can flap to different values on reingestion.

The write-visibility barrier was removed from save_doc_meta (documented at storage/verdict.py line 176-179) but retained in save_doc/save_flat_doc — a deliberate asymmetry that a future refactor could easily miss.

#### History

- **Chain 3 (RFC-034→036):** Write-visibility barrier over-provisioned at 4.4s caused PersistenceNotVisibleError propagated as RuntimeError, SLA document completed 3-5 minutes late, scored as false ERROR. (Improved zone: 10→5 bugs, MinIO-vs-Postgres divergence remediated) but left residual gap: MinIO sidecar still has no CAS equivalent to force_verdict_override.

- **Chain 28 (RFC-026):** Reingestion wipes processed/*.meta.json → find_prior_verdict always fails → hysteresis state lost → GHV-TKV-Tarif flapped PASS→MARGINAL on identical tree.

- **Chain 31:** Registry upsert_doc unconditionally overwrites verdict with empty string when sidecar omits it.

- **Chain 32:** Cabinet Decision 106/2022 stored verdict=PASS with empty reason despite session assessment of 40% Latin-mojibake garbling.

#### Code Evidence

- `_upsert_registry_row` (registry_mirror.py:55-155): Postgres pool check at line 96, Redis retry at line 99 ('await _enqueue_verdict_retry(doc_id, verdict_fields)'). CAS upsert at line 128 ('winning = await upsert_doc(fields, force_verdict_override=_force_override)'). Sidecar backfill at line 133-143 with exception swallowed at line 144-149.

- `save_doc_meta` (storage/verdict.py:78-185): read-merge-write pattern with _read_existing_sidecar at line 113, merge loop at lines 128-170, put_object at line 173. Write-visibility barrier removal documented at line 176-179: 'Zone-4 Phase 3: write-visibility barrier removed. Postgres is the sole verdict authority; the sidecar is archival-only'.

- `reconcile_registry_drift` (reconcile.py:109-228): drains Redis verdict retry queue at line 155, then does incremental O(delta) reconcile using etag-based change detection.

#### Key Files

- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/worker/registry_mirror.py
- src/pageindex_mcp/registry_backfill/reconcile.py
- src/pageindex_mcp/registry/queries.py

---

### Zone 6: Gate-to-Recovery Signal Threading Gaps

**Severity:** high | **Bug count:** 6

#### Mechanism

The generative mechanism is **ONE-DIRECTIONAL SIGNAL FLOW FROM GATE TABLE TO RECOVERY DISPATCH WITH REASON-STRING COUPLING**. `validate_tree` (tree_validation.py:262-354) returns the FIRST firing gate in GATE_TABLE order as the primary defect, but all_defects carries every co-firing gate. The GateSpec-driven recovery in gates.py now declares recovery_fns and recovery_eligible per gate (GARBLING has recovery_fns=('_recover_garble_ocr', '_recover_vlm_fallback') at gates.py:329), but this only works if the dispatching code reads ALL co-firing defects rather than just the primary.

The issue arises when garbled text is caught by NODE_GARBLING (severity=3) but the tree also fires NODE_COUNT_LOW (severity=1), making NODE_COUNT_LOW the primary defect since it appears earlier in gate order. NODE_COUNT_LOW routes to _recover_low_content_ocr rather than _recover_garble_ocr, so the garble-specific recovery never fires.

Separately, the 'fixed but never committed' pattern means correct recovery code exists in the working tree but never reaches production — seen with chunked_docling_timeout_s, _check_bidi_coherence, RFC-030 D6 judge calibration rules, and RFC-034 D19 enrichment-displacement guard.

#### History

- **Chain 6 (RFC-029→030):** Four new validate_tree failure reasons were never wired into client.py's recovery routing loop, causing 3 documents to flip PASS→ERROR in Run 13 — described as the single highest-impact systemic bug of that run.

- **Chain 19:** Early-exit ordering in validate_tree — documents with numeric-junk OCR text get NODE_COUNT_LOW reason instead of GARBLING, so OCR escalation (which only fires on 'garbling' reason) never triggers.

- **Chain 25:** VLM fallback only reachable post-OCR-01, not from D3B flat-path garble gate — rejection reason can shift to other code after OCR retry.

- **Chain 18 (RFC-034):** Enrichment-displacement guard staged in git working tree but never committed, stayed inactive through Run 19. Finally committed in RFC-036 D2. Same pattern with chunked_docling_timeout_s (RFC-027 task 4.2) and RFC-030 D6 judge calibration rules.

#### Code Evidence

- `GATE_TABLE` (gates.py:321-408): 10 gates with severity ordering. GARBLING severity=0, NODE_COUNT_LOW severity=1, NODE_GARBLING severity=3. GateSpec declares recovery_fns per gate: GARBLING at line 329 has recovery_fns=('_recover_garble_ocr', '_recover_vlm_fallback'), NODE_COUNT_LOW at line 337 has recovery_fns=('_recover_low_content_ocr', '_recover_image_dominant_ocr').

- `validate_tree` (tree_validation.py:319-331): iterates GATE_TABLE exhaustively, builds fired list, primary_defect = fired[0] (first in table order).

- `RecoveryMixin` (recovery.py): _execute_ocr_retry at lines 83-316 dispatches recovery based on defect; _recover_garble_ocr at lines 320-352.

- `GateSpec.recovery_eligible`: predicate gates whether recovery fires for a given defect.

#### Key Files

- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/client/indexer.py

---

### Zone 7: Remote vs. Local Execution Divergence

**Severity:** medium | **Bug count:** 5

#### Mechanism

The generative mechanism is **SPLIT EXECUTION CONTEXT WITH NO DEPLOYMENT SYNCHRONIZATION**. The BiDi heading-reversal guard (_heading_is_logical_order from RFC-033 D2) was implemented only in the local working tree and never committed (git log -S finds it in zero commits). The remote Scaleway Docling service runs a stale deployed image predating the guard, so remote-route documents still get every heading reversed.

The worker subprocess timeout was calibrated at 3x (RFC-032 D3), empirically shown insufficient (actual range 2.32x-11.00x), and recalibrated to 16.5x (RFC-032 D9) — but chunked_docling_timeout_s (RFC-027 task 4.2) was created but never wired to worker.py despite being marked complete, causing world-stats-pocketbook to timeout 3 consecutive runs.

REMOTE_MD_RENORMALIZE (config.py, default true) controls whether markdown from the remote route is renormalized, but this flag was added after the divergence was discovered rather than being part of the original design.

The AGPL fallback path (pymupdf4llm, Hard Rule 4) may fire on remote-Docling 504 timeouts without logging sufficient evidence to confirm or exclude it.

#### History

- **Chain 8 (RFC-033):** Guard was implemented only in local working tree and never committed. Remote Scaleway Docling service runs stale deployed image. Produced reversed headings for governance policy document on fresh ingest despite guard 'existing'.

- **Chain 9 (RFC-032→RFC-027 4.2→RFC-028):** 3x timeout insufficient (actual 2.32x-11.00x). chunked_docling_timeout_s created but never wired despite being marked complete. world-stats-pocketbook timed out ERROR, FAIL, ERROR across 3 consecutive runs.

- **Chain 10 (RFC-004):** pymupdf4llm still pulls PyMuPDF transitively. AGPL exposure has three independent entry points. BIDI_ROOT_CAUSE_RFC033 section 1.3 could neither confirm nor exclude AGPL fallback firing on a specific 504-timing-out remote-Docling document.

- **Chain 33:** Page rotation metadata lost in converter causes approximately 750 chars (9% of expected) extraction, stalled across Runs 8-10.

#### Code Evidence

- `REMOTE_MD_RENORMALIZE`: defined in config.py PipelineConfig (default true).

- `decide_ocr_strategy` (picture_plane.py:357-430): does not distinguish remote vs local route — the document_type parameter carries 'pdf'/'image' but not execution context.

- `RecoveryMixin._recover_landscape_reroute` (recovery.py): visible in symbols overview, is the only recovery method that implies route awareness.

- `ALLOW_AGPL_FALLBACK` (config.py PipelineConfig, default true): gates whether pymupdf4llm fallback may fire.

- The subprocess_mgr.py applies the 16.5x multiplier at approximately line 171-184.

#### Key Files

- src/pageindex_mcp/config.py
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/converters/headings.py
- src/pageindex_mcp/worker/subprocess_mgr.py

---

## Cross-Cutting Themes

- **Sequential remediation chains, not one-shot fixes:** Nearly every RFC's fix becomes the next RFC's root-cause finding (RFC-018→019→020 picture-OCR filter composition; RFC-021→022→023 verdict-gate/routing chain; RFC-024→025→026 leaf-ratio threshold saga; RFC-027→028→029→030 Arabic-recovery cascade; RFC-033→034→035→036 landscape/write-barrier cascade).

- **'Fixed but never wired/committed' is a recurring, distinct failure class:** chunked_docling_timeout_s, _check_bidi_coherence, RFC-029 D6 judge-calibration rules, and RFC-034 D19's enrichment-displacement guard were each correct in isolation but inert in production because nothing called or committed them — most eventually landed by later RFCs, narrowing but not eliminating the pattern.

- **Parameter/reason-string threading gaps make otherwise-correct detectors unfireable:** expected_script never passed to garble callers (RFC-019 D2), node_garbling never recognized by OCR-escalation (RFC-018 D3b), and RFC-029's four new validate_tree failure reasons never wired into recovery routing (caused Run 13's highest-impact systemic bug) — detection fires but its signal never reaches the code meant to act on it.

- **NFKC Unicode normalization is a recurring, independently-rediscovered blind spot for Arabic/RTL quality gates:** it silently destroys the presentation-form signal that both the garble detector and the bidi-coherence detector key on, found separately in RFC-028 D2, RFC-033 D2, and RFC-034 D7, and confirmed still structurally present as of the 2026-08-26 zone delta.

- **Threshold-widening without true anchoring produces recurrence cycles:** PASS_MAX_LEAF_RATIO was widened three times (0.17→0.20→0.30) chasing jitter on different documents, and even the eventual hysteresis fix (RFC-025 D0) was defeated by an orthogonal issue (corpus reingestion wiping the prior-verdict ledger).

- **Gates/mechanisms actively fighting each other:** the page-coverage OCR-skip filter vs. per-picture forced-OCR (chart garbling vs. scanned-page recovery); the full_page_already_applied re-entry guard vs. the newer UNIFIED_OCR_PLAN_ENABLED branch that bypasses it entirely; digit-ratio garble check vs. its 500-char floor duplicated in two non-shared functions.

- **Verdict-promotion / Hard-Rule-5 bypass is a persistent, worsening surface:** it evolved from implicit threshold-widening drift into an explicitly hard-coded, priority=100 escape hatch (image_enrichment_promoted) that outranks structural hard-fail verdicts by design — bug count fell but severity escalated because the bypass is now deliberate and actively maintained.

- **Null/zero-sensitivity detectors get misread as 'safe' rather than 'broken':** _check_bidi_coherence measured 0% true-positive rate (its only signal is structurally excluded from the range its own line-selector samples), yet BIDI_COHERENCE_ENFORCE was promoted to default-true on the reasoning that zero violations meant zero risk, rather than that the detector could not fire at all.

- **Table/structural-integrity destruction is caused by multiple independent pre-tree text transforms:** Arabic heading injection, the ordinal-matching oversized-leaf splitter, and _repair_docling_tables' degenerate-row collapse each independently fracture pipe-tables because no shared is_inside_table / table-span primitive exists across headings.py and tree_split.py.

- **AGPL exposure (Hard Rule 4) narrowing has been incremental and incomplete:** RFC-004's removal of the direct PyMuPDF dependency left pymupdf4llm's transitive pull open, consistent with the project's own memory note that AGPL exposure has three independent entry points (pymupdf4llm + docling-hierarchical-pdf + pageindex fork) that must all be verified together.

- **Dual-store/dual-CAS divergence for verdicts was structurally present:** MinIO sidecar vs. Postgres registry, each with its own priority map and CAS guard, until a 2026-08 fix designated Postgres as sole arbiter — but even that fix leaves the MinIO sidecar without an equivalent CAS guard, so a failed backfill can still silently disagree with the authoritative store.

- **Remote-vs-local execution divergence is an emerging, still-uninvestigated theme:** the BiDi heading-reversal root cause traced to a stale deployed remote Docling service image running code that predates a guard that exists only in the local working tree (never committed), and the 2026-08-26 zone delta separately flagged a new, unscoped 'Landscape/Rotation and Remote Route Divergence' zone for the same class of local/remote behavioral mismatch.

- **Corpus-audit scope narrowing is sometimes mistaken for remediation:** three zones (Tree-vs-Flat Gate Asymmetry, Worker/Inspector Dual-Threshold Race, HR3 PII Egress Gap) were marked 'closed' in the 2026-08-26 delta purely because their key files fell outside the current audit's scope, not because the underlying defects were confirmed fixed — explicitly flagged as needing independent re-verification.

- **Gate ordering and cascading short-circuits:** early-exit prevents garble signal, promotion-path bypasses hard rule, hysteresis relaxes detection.

- **Incomplete garble detection heuristics:** mixed-script false positives, Latin/European-language gaps, expected_script propagation breaks.

- **Threshold changes causing silent regression:** PASS_MAX_LEAF_RATIO widening, hysteresis band interactions, no test sync.

- **Architectural unreachability:** VLM fallback wiring gap in D3B, reordering check bifurcation between gates.

- **Data integrity breakdown:** fabricated corpus report, stored verdict divergence from assessment, harness ERROR defaults.

- **Hysteresis and persistence logic gaps:** wipe-before-reingestion breaks lookup, registry unconditional overwrite, verdict state lost.

- **Audit measurement unreliability:** harness substring matching bug, ERROR defaults, requires live MinIO re-pull verification.

- **Extraction quality regressions stalled across multiple runs:** page rotation coordinate mapping, numeric-junk OCR fragmentation.

---

## Simplification Proposals

### OCR Pipeline Filter Composition and Re-entry Hazards

1. CORE SIMPLIFICATION: Collapse decide_ocr_strategy's five ordered if/elif branches into one declarative, priority-ordered list of (guard, resolver) pairs so each rule is independently testable and order is explicit data, not control flow; delete the legacy decide_ocr_mode wrapper entirely. Replace the implicit cross-module `full_page_already_applied` boolean with an explicit OcrContext object threaded from recovery.py into picture_plane.py, and fix _text_layer_has_content so the char-count floor and garble check always run against the same post-clip_text extraction, removing the page-level short-circuit that skips clip_text.

2. RESTRUCTURING STEPS:
   - picture_plane.py (~357-456): merge decide_ocr_strategy + decide_ocr_mode into one function driven by an ordered rule list; delete decide_ocr_mode (~-40 lines net).
   - Add OcrContext dataclass (full_page_already_applied, unified_plan_enabled, force_full_page) passed explicitly recovery.py -> picture_plane.py instead of set/read on shared state (~+20 lines, new file section in picture_plane.py).
   - converters/pictures.py (~267-299): reorder _text_layer_has_content to compute clip_text once, then run char-count floor and garble check against it, removing the upstream short-circuit path (~-10/+10 lines, net neutral but removes a bug class).
   - client/indexer.py: replace duplicate-PictureResults marker-count workaround with an explicit marker-count parameter passed to splice_figure_markers instead of inferring count from duplicated results (~-15 lines).

3. BUG CLASSES PREVENTED: image-doc re-entry-guard bypass (the recurring Zone-2 bug the current comment documents as already having happened once); RFC-018→RFC-025 recurring char-count-floor-before-clip_text short-circuit; marker-count mismatches whenever picture classification changes.

4. MIGRATION RISK & SEQUENCING: Medium — touches every OCR routing call site. Sequence: (a) pin current behavior with unit tests for all 5 existing branches before touching anything; (b) introduce OcrContext + rule-list under the existing function name/signature (no behavior change); (c) delete decide_ocr_mode once all callers use the consolidated function; (d) fix _text_layer_has_content ordering last since it changes actual OCR trigger rates — validate against corpus fixtures before merging.

5. ESTIMATED EFFORT: 3-4 days.

### Garble Detection Surface Fragmentation

1. CORE SIMPLIFICATION: Make had_presentation_forms a mandatory parameter into detect_garble sourced only from ScriptContext.from_document (computed pre-NFKC), and delete the unreliable post-normalization fallback scan inside detect_garble. Replace the short_text_prior_garble unconditional `is_garbled=True` short-circuit with a soft weighted prior that still lets the fast prongs vote, so a single prior verdict can no longer permanently poison retries on short text (fixing non-idempotence).

2. RESTRUCTURING STEPS:
   - script.py / garble.py: require had_presentation_forms as a non-Optional argument to detect_garble; delete the fallback blob-scan at garble.py lines ~541-543 (~-15 lines).
   - garble.py (~524-534): change short_text_prior_garble from a hard return to adding a weighted prong into the normal garble_prongs vote, so digit_ratio/presentation-forms prongs still execute on retried short text (~+10/-10 lines, behavior change).
   - garble.py latin_gibberish prong (~392): give _script_from_filename a documented fallback (`expected_script or detected_script`) so the prong isn't permanently unfireable for German filenames (~+10 lines), gated behind a config flag for initial rollout.
   - garble_digit_floor: make configurable per corpus/language rather than a fixed 500-char cutoff, documented as an explicit trade-off (~+5 lines, config.py).

3. BUG CLASSES PREVENTED: presentation-form signal loss from NFKC-before-detect ordering; non-idempotent short-text poisoning across OCR retries; permanently disabled latin_gibberish prong for German documents; uninspected short garbled text below the digit floor.

4. MIGRATION RISK & SEQUENCING: Low-medium, isolated to garble.py/script.py. Sequence: (a) make had_presentation_forms mandatory + delete fallback — pure refactor if callers already pass ScriptContext, low risk; (b) convert short-circuit to soft prior — behavior change, run full corpus regression before merging; (c) latin_gibberish fallback last, behind a flag, since it changes fire rate specifically on the German corpus (the first validation vertical per CLAUDE.md).

5. ESTIMATED EFFORT: 2-3 days.

### Verdict Gate Promotion Bypass Cascade

1. CORE SIMPLIFICATION: Remove the `if not _has_image_rescue` guard around the max_leaf_ratio hard-fail check so hard-fail is always evaluated unconditionally first; image_enrichment_promoted then competes only among promotion candidates and can never bypass a fired HARD_FAIL. Unify _try_image_enrichment's PASS/MARGINAL branches to use the same char floor and give MARGINAL a strictly lower priority than genuine structural PASS candidates, so a below-floor result can no longer outrank real passes at priority=100.

2. RESTRUCTURING STEPS:
   - verdict.py apply_promotions (~407-518): delete the `if not _has_image_rescue` conditional around the hard-fail check (~line 476); hard-fail is now evaluated unconditionally before promotions are considered (~-10/+5 lines, net simpler control flow).
   - verdict.py _try_image_enrichment (~220-270): split priority tiers — PASS keeps priority=100, MARGINAL drops to a lower fixed priority so it can't crowd out structural passes (~+10 lines).
   - tree_validation.py validate_tree (~262-354): expose the full all_defects list as the primary return alongside (or instead of) the first-firing 2-tuple; update call sites to consume the list so co-firing gates are no longer silently dropped (~+20/-10 lines across validate_tree and its callers).
   - config.py: consolidate PASS_MAX_LEAF_RATIO and hysteresis-relaxation logic into one documented threshold module instead of independently-tunable constants (removes the circular-coupling root cause).

3. BUG CLASSES PREVENTED: garbled documents promoted via image-rescue bypass of hard-fail; MARGINAL-at-priority-100 crowding out genuine structural PASS; hysteresis relaxing max_leaf_ratio and reintroducing garbling on re-score; loss of co-firing gate information at call sites.

4. MIGRATION RISK & SEQUENCING: High — this changes real pass/fail outcomes. Sequence: (a) add the all_defects-returning API alongside the existing 2-tuple first — additive, zero risk; (b) run the full corpus in shadow mode with hard-fail-always-evaluated and diff against current verdicts before flipping any behavior; (c) only then remove the bypass guard, reviewed against the shadow diff; (d) unify hysteresis/threshold config last, sequenced together with the Zone-5 fix to processed/*.meta.json persistence since hysteresis-ledger loss on reingestion is a shared root cause.

5. ESTIMATED EFFORT: 4-5 days (dominated by corpus regression validation, not code volume).

### Pre-Tree Text Transform Table Fracture

1. CORE SIMPLIFICATION: Wire split_oversized_leaf_nodes to call the already-existing, already-imported-elsewhere compute_table_spans/line_in_table_span (same tree_split.py module, already proven correct in headings.py's three heading injectors) before running its line-level splitters — no new abstraction, just reuse.

2. RESTRUCTURING STEPS:
   - tree_split.py split_oversized_leaf_nodes (~401-477): compute `table_spans = compute_table_spans(lines)` once at function entry; guard _split_on_paragraph_markers, _split_on_blank_line_paragraphs, _split_on_atx_headings, and _split_on_generic_numbered_lines with `line_in_table_span` checks so table rows are never treated as split boundaries (~+30-40 lines, no new functions — reusing existing ones).
   - No deletions needed beyond removing any ad hoc table-guessing inline in the splitters if present.

3. BUG CLASSES PREVENTED: pipe-table rows split mid-row by ordinal/ATX/paragraph/blank-line splitters; table fracture across child nodes that downstream _segment_table_nodes/_repair_docling_tables cannot repair because it runs after the damage is already done.

4. MIGRATION RISK & SEQUENCING: Low — purely additive guards using already-tested, already-imported functions. Sequence: (a) add table_spans computation + guards behind a feature flag; (b) run a corpus diff confirming table-containing docs stop fracturing and non-table docs are byte-identical; (c) remove the flag once validated.

5. ESTIMATED EFFORT: 1 day.

### Verdict Persistence Competing Writers

1. CORE SIMPLIFICATION: Make Postgres the sole verdict authority in fact, not just in comment, by removing the inline best-effort MinIO sidecar backfill from _upsert_registry_row and making reconcile_registry_drift (already doing incremental etag-based reconcile) the single writer of the sidecar, driven durably by the existing Redis retry queue instead of a swallowed-exception inline write. This eliminates the dual-writer race instead of trying to keep two writers consistent.

2. RESTRUCTURING STEPS:
   - worker/registry_mirror.py _upsert_registry_row (~133-149): remove the inline save_doc_meta backfill call and its swallowed exception handler; always enqueue a sidecar-sync entry onto the existing Redis queue regardless of Postgres CAS outcome (~-20/+10 lines).
   - registry_backfill/reconcile.py reconcile_registry_drift: widen its existing role to be the single consumer of that queue and the sole sidecar writer (~+15 lines, reusing existing incremental-reconcile machinery).
   - storage/verdict.py save_doc_meta (~78-185): add a CAS/etag guard even for its now-single caller so two concurrent reconcile passes can't lose an update (~+15 lines).
   - Fix _enqueue_verdict_retry to log/metric on failure instead of swallowing it silently, relying on reconcile's periodic drift scan as defense in depth (~+5 lines).
   - Stop wiping processed/*.meta.json on reingestion — merge-preserve the fields find_prior_verdict needs for hysteresis instead of overwriting, since destroying this ledger causes verdict flapping (shared root cause with Zone 3's hysteresis coupling) (~+10/-5 lines in the reingestion path).

3. BUG CLASSES PREVENTED: stale MinIO sidecar after Postgres CAS succeeds but inline backfill fails silently; hysteresis-ledger wipe on reingestion causing verdict flapping; silently swallowed Redis-retry-enqueue failures leaving the sidecar permanently stale.

4. MIGRATION RISK & SEQUENCING: Medium — changes the write path for every ingested doc. Sequence: (a) add reconcile-driven sidecar write as an additive path alongside the current inline backfill and diff the two outputs; (b) once diffed clean, remove the inline backfill from _upsert_registry_row; (c) fix meta.json wipe-on-reingest last as an independent, easily isolated change (merge instead of overwrite).

5. ESTIMATED EFFORT: 2-3 days.
