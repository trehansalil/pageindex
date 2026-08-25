---
title: Architecture Defect Zones Audit — 2026-08-12 POST
date: 2026-08-12
type: audit/defect-zones
tags:
  - audit
  - defect-zones
  - architecture
  - post-fix
aliases:
  - POST audit
  - 2026-08-12 defect zones
prior_audit: "[[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11]]"
delta_report: "[[ZONE_DELTA_2026-08-12_POST]]"
scorecard: "[[REMEDIATION_SCORECARD_2026-08-12_POST]]"
verdict: REGRESSED
zone_count: 7
bug_count: 52
---

# Architecture Defect Zones Audit — 2026-08-12 POST

**Date:** 2026-08-12
**Sources:** 18 history miners, 1 code maps
**Prior audit:** [[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11]]
**Delta report:** [[ZONE_DELTA_2026-08-12_POST]]
**Scorecard:** [[REMEDIATION_SCORECARD_2026-08-12_POST]]

## Summary Table
| # | Zone | Severity | Bug Count | Key Files |
|---|------|----------|-----------|-----------|
| 1 | Garble Detection Fragmentation | critical | 18 | `src/pageindex_mcp/helpers/garble.py`, `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/helpers/tree_validation.py`, `src/pageindex_mcp/helpers/verdict.py` |
| 2 | OCR Strategy Bifurcation | critical | 15 | `src/pageindex_mcp/picture_plane.py`, `src/pageindex_mcp/converters/pictures.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/client/images.py` |
| 3 | Verdict Promotion / Quality Gate Stack | critical | 14 | `src/pageindex_mcp/helpers/verdict.py`, `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/registry/queries.py`, `src/pageindex_mcp/client/indexer.py` |
| 4 | Multi-Store Dual-Write Consistency | high | 11 | `src/pageindex_mcp/storage/documents.py`, `src/pageindex_mcp/worker/registry_mirror.py`, `src/pageindex_mcp/registry/queries.py`, `src/pageindex_mcp/worker/job.py`, `src/pageindex_mcp/storage/hash_cache.py` |
| 5 | God-Function Orchestration with Duplicated Divergent Logic | high | 10 | `src/pageindex_mcp/worker/subprocess_mgr.py`, `src/pageindex_mcp/worker/job.py`, `src/pageindex_mcp/storage/documents.py`, `src/pageindex_mcp/helpers/flat.py`, `src/pageindex_mcp/helpers/tree_split.py`, `src/pageindex_mcp/helpers/tables.py` |
| 6 | validate_tree Reason-String Dispatch | high | 9 | `src/pageindex_mcp/helpers/tree_validation.py`, `src/pageindex_mcp/helpers/gates.py`, `src/pageindex_mcp/client/indexer.py`, `src/pageindex_mcp/client/recovery.py` |
| 7 | Config Layering Split and Dead-Code Accumulation | medium | 7 | `src/pageindex_mcp/config.py`, `src/pageindex_mcp/converters/pipeline.py`, `src/pageindex_mcp/helpers/garble.py` |

## Zone Details

### Zone 1: Garble Detection Fragmentation
**Severity:** critical | **Bug count:** 18

#### Mechanism
Each prong has an independent boolean/threshold gate that can be silently disabled without affecting any other prong. When a new corruption type is discovered, a new prong is added but (a) its gating condition may be mutually exclusive with its detection target (Latin-gibberish guarded by expected_script != 'Latn'), (b) its pre-computation requirement may be destroyed by an upstream normalization step (NFKC decomposing Presentation-Forms codepoints), or (c) its length floor may be undercut by a different subsystem's decomposition (per-node splitting producing sub-500-char chunks). Fixing one prong's sensitivity routinely breaks an adjacent prong's calibration (e.g., improving OCR language detection diluted the digit-ratio garble signal, letting junk through). The validate_tree orchestrator produces a fully silent OK with zero secondary signal when all prongs miss, so there is no 'low-confidence garble' warning path -- a miss is indistinguishable from genuinely clean content.

#### History
a. RFC-010 D3/D3B: token-repetition guard duplicated in two functions, fixed RFC-013 D7.
b. RFC-019 D2: Latin-gibberish check guarded by expected_script != 'Latn' but callers never pass it.
c. RFC-020: same gap reconfirmed, callers still not passing expected_script.
d. RFC-023 D3: image-marker token repetition false positive ('image' at 100% ratio).
e. RFC-024: PASS_MAX_LEAF_RATIO relaxation let 81/132 garbled nodes PASS with empty verdict_reason.
f. RFC-028 D2: Arabic Presentation-Forms detection added -> RFC-029 D0: reason=garbling excluded from flat routing, producing terminal ERROR with zero artifacts.
g. RFC-028 D3: RTL reversal detection vocabulary too small (14 words), zero true-positive on governance docs.
h. RFC-029 D2: improved OCR language detection paradoxically removed garble-gate safety net on junk text.
i. RFC-030 D4: _garble_check_nodes only inspects node.get('text'), never 'title' -- 23/24 reversed node titles invisible.
j. RFC-030 D5: _check_bidi_coherence fully implemented but never wired -- dead code.
k. Runs 10-13: garble gate oscillated on MOU (ratio=1.00 on clean Arabic, then corrected, then reappeared) and ward 597 (garbled_blocks=0 despite visible garbling).
l. Run 15: exact garble false-positive from Run 13 reappeared after Run 14 correction.
m. Zone triage 2026-08-21: NFKC normalization destroys Presentation-Forms before _reversed_morphology, yielding structural 0% true-positive rate; four call sites pass throwaway ScriptContext(had_presentation_forms=False).
n. Observation #5330: 10 production check_garble calls use legacy had_presentation_forms=False.

#### Code Evidence
`src/pageindex_mcp/helpers/garble.py:384` garble_prongs digit_ratio gated by `if len(norm) > cfg.garble_digit_floor` (VERIFIED 500 chars). `src/pageindex_mcp/helpers/garble.py:463` GarbleConfig.from_config() hardcodes `garble_digit_floor=500` literally instead of `cfg.garble_digit_floor` despite reading the other 6 fields from cfg (VERIFIED). `src/pageindex_mcp/helpers/garble.py:395-403` latin_gibberish prong gated by `_effective_script != 'Latn'` -- unreachable for German/English primary corpus (VERIFIED). `src/pageindex_mcp/helpers/tree_validation.py:235-308` validate_tree runs all 10 GATE_TABLE entries exhaustively, returns TreeGateResult with fully silent OK/empty all_defects when nothing fires (VERIFIED). `src/pageindex_mcp/helpers/garble.py:318-409` garble_prongs signature: expected_script defaults to None (VERIFIED). `src/pageindex_mcp/helpers/verdict.py:400` classify_verdict: expected_script=None default (VERIFIED via search_code).

#### Key Files
- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/helpers/verdict.py

#### Simplification Proposal
**(1) Core Simplification**

Replace the nine independent boolean-gated prongs in `garble_prongs()` with a single `GarbleEvaluation` pipeline that (a) runs each prong as a named step receiving a shared, immutable `GarbleInput` dataclass (containing pre-NFKC raw text, post-normalization text, effective script, had_presentation_forms, and config), (b) returns a structured per-prong result (fired: bool, confidence: float, detail: str) collected into a single list, and (c) makes every prong's preconditions (length floor, script filter) declarative fields on the step rather than inline `if` guards buried in a 90-line function body. The `detect_garble` function becomes the sole constructor of `GarbleInput`, guaranteeing that presentation-forms detection always runs on raw text before NFKC normalization, and that `had_presentation_forms` is never a throwaway `False`. The hardcoded `garble_digit_floor=500` literal in `from_config` is replaced with `cfg.garble_digit_floor`, and `garble_digit_floor` is added to `PipelineConfig` so it becomes configurable like the other six fields.

**(2) Concrete Restructuring Steps**

Step A -- Fix the config bug (garble.py, config.py; +3 lines net): `garble.py:463`: change `garble_digit_floor=500` to `garble_digit_floor=cfg.garble_digit_floor`. `src/pageindex_mcp/config.py` PipelineConfig: add `garble_digit_floor: int = 500` field (read from env var `GARBLE_DIGIT_FLOOR`, default 500).

Step B -- Eliminate throwaway ScriptContext construction (garble.py, tree_validation.py, verdict.py, indexer.py, recovery.py, pictures.py, images.py; ~-30 lines net): move the presentation-forms pre-NFKC scan into `ScriptContext.from_raw_text` as the single source of truth; update the 11 call sites currently constructing `ScriptContext(had_presentation_forms=False, ...)`; remove the duplicate presentation-forms scan inside `detect_garble`.

Step C -- Fix latin_gibberish unreachability (garble.py:389-401; +5/-3 lines): change the guard so the prong runs when `_effective_script is None` or `_effective_script == "Latn"`, gated only by `cfg.garble_latin_gibberish_enabled`.

Step D -- Declarative prong registry (garble.py; ~+40/-60 lines net): extract each prong into a named function `(input: GarbleInput) -> ProngResult | None`; create a `PRONG_TABLE` (parallel to `GATE_TABLE`) declaring name, function, min_length, script_filter, requires_raw_text; `garble_prongs()` becomes a loop over `PRONG_TABLE`.

Step E -- Low-confidence garble warning path (tree_validation.py:303-308; +8 lines): when `validate_tree` returns OK, check whether any prong fired at sub-threshold confidence and populate a new `warnings: list[str]` field on `TreeGateResult` (default empty). No behavioral change to `ok`.

Step F -- Per-node garble uses concatenated text, not individual chunks (garble.py `_garble_check_nodes`; ~+5/-3 lines): add a secondary whole-tree check on concatenated node text when no per-node garbling was detected but the total exceeds `garble_digit_floor`; also inspect `title` fields.

Estimated line-count delta across all steps: roughly +30 net.

**(3) Historical Bug Classes Prevented**

RFC-019 D2 / RFC-020 (latin_gibberish unreachable for Latin corpus): Step C fixes the inverted guard directly. RFC-028 D2 / NFKC-destroys-Presentation-Forms (structural 0% true-positive): Step B ensures had_presentation_forms is always computed from raw text before NFKC. RFC-029 D2 (improved OCR language detection removed garble safety net): declarative PRONG_TABLE makes preconditions reviewable together. RFC-030 D4 (_garble_check_nodes ignoring title): Step F adds title inspection. RFC-030 D5 (_check_bidi_coherence dead code): PRONG_TABLE makes unwired prongs visible at import time. Runs 10-15 oscillation: the low-confidence warning (Step E) would have surfaced sub-threshold signals earlier. garble_digit_floor hardcoded 500: Step A fixes directly. Per-node decomposition undercutting digit_floor: Step F adds concatenated fallback.

**(4) Migration Risk and Sequencing**

Risk is moderate -- garble detection is load-bearing for validate_tree (CLAUDE.md HR5). Sequence: 1) Step A (config fix, zero risk). 2) Step C (latin_gibberish guard fix, small isolated change, run corpus scoring before/after to measure delta). 3) Step B (eliminate throwaway ScriptContext, moderate risk, corpus diff on German T&C PDFs to verify no false-positive explosion). 4) Step D (declarative PRONG_TABLE, pure refactor, requires full parity test coverage). 5) Step F (per-node concatenated fallback + title inspection, behavioral change, corpus diff required). 6) Step E last (additive warning path only). Each step has a clean rollback; Steps A-C are independent one-to-three-line changes.

**(5) Estimated Effort**

Step A: 0.5h. Step B: 3-4h. Step C: 1h. Step D: 4-5h. Step E: 2h. Step F: 2-3h. Total: ~13-16h implementation plus 3-4h corpus scoring verification -- roughly two developer-days.

---

### Zone 2: OCR Strategy Bifurcation
**Severity:** critical | **Bug count:** 15

#### Mechanism
When a document-class-specific filter is added to one OCR path (e.g., >60% page-coverage skip to avoid wasted OCR on decorative backgrounds), it applies uniformly to all document types including those where the filtered condition IS the content (scanned PDFs where the picture IS the full page). Forced-OCR decisions in the pre-garble probe strip PictureItems from Docling output (reclassified as TextItems), destroying the per-picture OCR path's input. The tree path never calls splice_figure_markers before md_to_tree, so picture-recovered text is invisible to tree construction. OCR retry unconditionally replaces md_content without comparing pre-retry vs post-retry quality, persisting regressions when retry produces less content. Language detection derived from near-empty markdown returns wrong languages for non-Latin scanned PDFs. Each fix is designed for one document class but tested only against that class, blind to the structural coupling with other paths.

#### History
a. RFC-015 D6 -> RFC-017 D0: per-picture OCR pipeline conflated with proven page-level OCR escalation pipeline, causing OCR-recovered text to be structurally reclassified from prose blocks into image-block ocr_text fields.
b. RFC-017 D1: standalone images never call splice_figure_markers, pic_results stays empty.
c. RFC-018 D0 -> RFC-020 Regression 1: page-coverage >60% filter unconditionally skipped ALL full-page regions including genuine scanned pages -- five Arabic scanned PDFs regressed.
d. RFC-018 D3a -> RFC-020 Regression 1 Cause 3: forced OCR did not pass ocr_lang_override, defaulting to deu,eng for Arabic docs.
e. RFC-019 D1 + D0 -> RFC-020 Regression 2: combined coverage + clip-text filters killed ALL picture regions in image-heavy docs.
f. RFC-020: tree path never calls splice_figure_markers before md_to_tree, markdown becomes nearly empty -> depth<2 -> flat-routing.
g. RFC-023 D0: F1 coverage exemption only checks character count without garble detection.
h. RFC-027 D2 -> RFC-028 D4: OCR retry unconditionally replaces md_content, causing content regression when retry produces fewer chars.
i. RFC-028 D5: language detection derived from near-empty Docling markdown returned ['eng'] for scanned Arabic PDFs.
j. RFC-030 D1: _repeating_token_density hardcoded 0.0 for text <20 tokens, making OCR retry win condition arithmetically impossible for no-text-layer PDFs.
k. Run 3/4: F2+D2 forced-OCR side effect -- Docling reclassifies PictureItems as TextItems under forced OCR, 0 PictureResults, F0 splice fails.
l. Cross-cutting investigation 2026-07-27: standalone image branch bypasses enrichment entirely, confirmed data loss on pie chart numeric labels.

#### Code Evidence
picture_plane.py:344-386 decide_ocr_strategy: only called from .pdf branch, never for standalone images (VERIFIED -- 3 callers, all in PDF paths). client/indexer.py:662-665: standalone image Tesseract fallback with MIN_STANDALONE_IMAGE_MD_CHARS (duplicated constant in images.py:77 and indexer.py:227). converters/pictures.py:249-259 _tesseract_ocr_image: bare except Exception returns '' silently -- no metric, no propagation. converters/pictures.py:1007-1013 splice_figure_markers: marker/pic-count mismatch check. client/images.py:86-102 apply_image_ext_content_class_override: forces content_class='image_standalone', IMAGE_STANDALONE_PIPELINE_ENABLED checked twice independently.

#### Key Files
- src/pageindex_mcp/picture_plane.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/client/images.py

#### Simplification Proposal
**(1) Core simplification**

Replace the three independent OCR entry points (page-level escalation in the PDF branch, per-picture crop OCR in `_recover_picture_text`, standalone-image OCR hardcoded in the `elif ext in _IMAGE_EXTS` block) with a single `OcrPlan` produced by an expanded `decide_ocr_strategy()` that covers ALL document types -- not just PDFs. The standalone-image branch currently bypasses `decide_ocr_strategy` entirely, hardcodes `["ara", "deu", "eng"]` instead of calling `detect_ocr_langs`, skips `splice_picture_text_for_tree`, and duplicates `MIN_STANDALONE_IMAGE_MD_CHARS` across two files. Routing every file type through one decision point eliminates the structural coupling where a filter added for one document class silently degrades another.

**(2) Concrete restructuring steps**

Step A -- Extend `decide_ocr_strategy` in `src/pageindex_mcp/picture_plane.py` to accept a `document_type: Literal["pdf","image","html","text","xlsx"]` parameter and produce an `OcrPlan` (rename/extend `OcrDecision`) that also carries `ocr_langs: list[str]` and `splice_required: bool`. ~+30 lines.

Step B -- In `indexer.py`, replace the `elif ext in _IMAGE_EXTS` block (lines 656-685) with a call to the unified `decide_ocr_strategy(document_type="image", ...)`, then a shared `_execute_ocr_plan(plan, state)` helper that dispatches OCR and calls `splice_picture_text_for_tree` when `plan.splice_required`. Net: ~-20 lines.

Step C -- Delete the duplicated `MIN_STANDALONE_IMAGE_MD_CHARS` constant from `src/pageindex_mcp/client/images.py:77`; import from the canonical location in indexer.py. ~-1/+1 lines.

Step D -- In `src/pageindex_mcp/converters/pictures.py`, `_tesseract_ocr_image` (lines 249-259): replace bare `except Exception` with specific exceptions and a Prometheus counter for OCR failures. ~+5 lines.

Step E -- Add a quality comparison guard in the OCR retry path: only replace `md_content` with retry output when the retry produces more content or passes a garble gate the original failed. ~5-line conditional.

Step F -- In `indexer.py` line 658, replace the hardcoded `["ara","deu","eng"]` with `detect_ocr_langs(filename)`. 1-line change.

Estimated net delta: ~+15 lines across 4 files.

**(3) Historical bug classes prevented**

RFC-018 D0 / RFC-020 Regression 1 (coverage filter killing scanned pages): unified decision point with document_type awareness. RFC-018 D3a (forced OCR defaulting to deu,eng): Steps F+A eliminate all hardcoded language lists. RFC-020 (tree path never calling splice_figure_markers): Step B makes splice mandatory when plan.splice_required. RFC-027 D2 / RFC-028 D4 (OCR retry unconditional replacement): Step E prevents content regression. RFC-028 D5 (language detection from near-empty markdown): plan-level language decision (Step A) removes the empty-markdown fallback. RFC-030 D1 (density metric returning 0.0 for short text): Step E's quality guard provides a fallback. Run 3/4 F2+D2 (forced OCR zeroing PictureResults): unified plan tracks full_page_already_applied. Cross-cutting 2026-07-27 (standalone image bypassing enrichment): Steps A+B route standalone images through the same enrichment path as PDFs.

**(4) Migration risk and sequencing**

Main danger: changing the OCR decision flow for PDFs while fixing images could regress the working PDF path. Sequence: 1) Step F (1-line, zero risk). 2) Step C (constant dedup, pure cleanup). 3) Step D (exception narrowing + metric, observability only). 4) Step E (quality guard, additive, feature-flag via `OCR_RETRY_QUALITY_GUARD_ENABLED`). 5) Steps A+B together (structural change, feature-flag `UNIFIED_OCR_PLAN_ENABLED` default false, shadow-mode comparison for one release cycle before flipping default).

**(5) Estimated effort**

Steps F+C+D: 1-2h. Step E: 2-3h. Steps A+B: 6-8h including shadow-mode scaffolding. Total: ~2 days, deployable in 4 independent PRs.

---

### Zone 3: Verdict Promotion / Quality Gate Stack
**Severity:** critical | **Bug count:** 14

#### Mechanism
The promotion stack in apply_promotions() tries each rescue path in sequence; when a rescue fires, it bypasses all subsequent gates. A promotion path that lacks a content-volume floor (e.g., image_enrichment_promoted with no MIN_IMAGE_PROMOTED_CHARS check before RFC-023 D4) lets zero-content documents PASS. The downstream verdict CAS (_UPSERT_SQL) compares verdict priorities (PASS=3>MARGINAL=2>FAIL=1>ERROR=0) and can only upgrade or tie, never downgrade -- so a document that silently passes validate_tree due to a garble-gate blind spot gets a PASS verdict that is permanently locked in Postgres. A later, better garble check that correctly reclassifies the same doc as FAIL cannot self-heal the stored verdict through the normal upsert path. Threshold changes (e.g., low_content_density from 500 to 150 chars/node, PASS_MAX_LEAF_RATIO from 0.17 to 0.30) are calibrated against one problematic document but affect the entire corpus distribution, causing oscillation between over-rejection and under-rejection across consecutive runs.

#### History
a. RFC-022 B2: QF2a promotion unreachable for max_leaf_ratio>0.75 -- hard-FAIL gate at helpers.py:1184 fires before QF2a check at line 1245.
b. RFC-022 B1: structure=[] produces degenerate metrics (node_count=0, effectively_garbled=True), blocking all promotion gates.
c. RFC-023 D4: synthetic structure from 15 flat blocks (210 total chars, all '<!-- image -->') passed node_count>=3, producing factually wrong PASS -- remediated with MIN_FLAT_PROMOTION_CHARS.
d. RFC-025 Run 9: image_enrichment_promoted assigned PASS with 38 chars (barcode watermark), less than prior run's 60-char FAIL.
e. RFC-025: garble detection correctly flagged garbling(ratio=1.00) but no escalation hook -- persisted fully-garbled text as MARGINAL.
f. RFC-029 D1: heading injection gave shallow Arabic trees just enough depth to clear validate_tree, blocking richer flat fallback -- 80% content loss.
g. RFC-030 D2: four new validate_tree reasons (suspect_density, low_content_density, empty_node_contamination, arabic_low_content_ratio) unhandled by client.py if/elif chain -- fell through to raise LowQualityTreeError, causing 3 PASS->ERROR regressions.
h. RFC-030 D3: low_content_density threshold of 500 chars/node calibrated against one doc, over-rejected legitimate legal trees in 300-500 range (Penal Code 408.2, federal_decree_law_no_33 54.3, marsoom 33 459.4).
i. RFC-030 D6: RFC-029 D6 Phase B judge-calibration rules marked complete but never written to SKILL.md.
j. RFC-036 D4: landscape_fallback_picture PictureResults with skipped_reason triggered false image_enrichment_promoted verdicts.
k. Run 14: low_content_density gate removal caused federal_decree_law oscillation PASS->MARGINAL->PASS across Runs 15-16.

#### Code Evidence
helpers/verdict.py:219-347 apply_promotions(): image_enrichment_promoted path at lines ~268-285 checks image_enrichment_ratio >= 0.8 and total_chars < th.min_image_promoted_chars, but this guard was added later (VERIFIED). helpers/verdict.py:118-216 evaluate_gates(): HARD_FAIL_DEFECTS check, hard_fail_verdict returned directly skipping Phase 2 promotions (VERIFIED). registry/queries.py:19-91 _UPSERT_SQL: verdict-priority CASE repeated verbatim across 4 column assignments (verdict, pipeline_version, permanent_marginal, verdict_computed_at) -- identical CASE WHEN expression with PASS=3>MARGINAL=2>FAIL=1>ERROR=0, can only upgrade or tie (VERIFIED). helpers/verdict.py:350-391 compute_verdict dispatches to evaluate_gates then apply_promotions (VERIFIED).

#### Key Files
- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/registry/queries.py
- src/pageindex_mcp/client/indexer.py

#### Simplification Proposal
**(1) Core simplification**

Replace the sequential promotion cascade in `apply_promotions()` with a score-all-then-pick-best pattern: evaluate every eligible promotion path, collect each candidate `(verdict, reason)` into a list with its content-volume evidence, then select the highest-quality candidate that passes a uniform content-floor guard. In the Postgres upsert, replace the 4x copy-pasted verdict-priority CASE expression with a single SQL function referencing the canonical `VERDICT_PRIORITY` dict, and add an explicit `force_downgrade` boolean so a re-ingestion with improved gates can override a previously locked verdict when the pipeline version is newer.

**(2) Concrete restructuring steps**

Step A -- `src/pageindex_mcp/helpers/verdict.py`: refactor `apply_promotions()` to build a list of `PromotionCandidate(verdict, reason, char_count, garble_clean)`, filter below `min_promoted_chars`, select the best surviving candidate. ~+30 lines.

Step B -- `src/pageindex_mcp/helpers/verdict.py`: extract each promotion path into a named function `_try_image_enrichment(...)` etc., each owning its own content-volume floor check. ~+20/-15 lines.

Step C -- `src/pageindex_mcp/registry/queries.py`: extract the 4x duplicated verdict-priority CASE into a single Postgres function `verdict_priority(text) RETURNS int`; add `force_verdict_override` parameter to `upsert_doc()`. ~-25/+15 lines.

Step D -- `src/pageindex_mcp/registry/queries.py` + `src/pageindex_mcp/helpers/types.py`: generate the SQL function's priority mapping from `VERDICT_PRIORITY` dict at migration time. ~+10 lines.

Step E -- `src/pageindex_mcp/client/indexer.py`: wire `force_verdict_override` into re-ingestion calls when `pipeline_version` is strictly newer. ~+5 lines.

Net delta: roughly +30 lines.

**(3) Historical bug classes prevented**

RFC-025 Run 9 (image_enrichment_promoted with 38 chars): universal content-floor filter in Step A rejects any candidate below the floor regardless of evaluation order. RFC-023 D4 (synthetic 210-char flat blocks): per-candidate char floor applied uniformly. RFC-022 B2 (QF2a unreachable because hard-FAIL fires first): score-all evaluation order no longer silently suppresses later paths. RFC-030 D3 (threshold calibrated against one doc): candidate-list approach gives corpus-wide visibility into near-miss promotions before changing a threshold. Verdict lock-in (all runs): `force_verdict_override` allows a newer pipeline version to downgrade a previously mis-promoted PASS. RFC-036 D4 (landscape_fallback_picture false promotion): isolated `_try_image_enrichment` is unit-testable. Would NOT have prevented RFC-030 D2 (unhandled new reasons in client.py) -- separately addressed by the GATES registry.

**(4) Migration risk and sequencing**

PR1 (low risk): extract verdict-priority CASE into a Postgres function and add `force_verdict_override` (default False, no behavioral change). PR2 (medium risk): refactor `apply_promotions()` to score-all-then-pick; must reproduce existing precedence (RFC-022 B2) via candidate priority weights, not evaluation order -- run in shadow mode comparing outputs on full corpus before switching, assert identical (verdict, reason) for every doc. PR3 (medium risk): wire `force_verdict_override=True` into re-ingestion when pipeline_version is newer -- first behavioral change allowing downgrade of locked PASS verdicts; gate behind `VERDICT_DOWNGRADE_ENABLED` (default False).

**(5) Estimated effort**

PR1: 0.5 day. PR2: 2-3 days (including corpus comparison tooling). PR3: 0.5 day. Total: 3-4 days implementation plus 1 day corpus validation.

---

### Zone 4: Multi-Store Dual-Write Consistency
**Severity:** high | **Bug count:** 11

#### Mechanism
The worker's child process writes MinIO artifacts, then the parent process upserts Postgres via _upsert_registry_row. If the MinIO write is not yet read-visible when the scorer or registry_mirror reads it, the Postgres row gets partial/empty data. The stale_row_delete_gate in reconcile treats empty/unparseable processed_at as 'old enough to delete', so a partial-data row from a failed dual-write gets deleted by the next reconcile tick (~20 min). The hash_cache_delete only issues Redis HDEL -- never touches the legacy MinIO blob hashes/processed_hashes.json, leaving a filename+hash correlation (potentially PII-bearing) surviving erasure indefinitely, violating Hard Rule 2. Raw staged uploads (uploads/staging/<job_id>/<filename>) are keyed by job_id with no stored linkage to doc_id, placing them outside delete_doc's uploads/<doc_id>/ scan by construction.

#### History
a. RFC-002 Amendment 2: delete_doc cascade order reversed (Redis->processed->meta->uploads instead of spec order) and hash-cache leak allowing re-upload dedup to deleted doc.
b. RFC-006 D1/D2 -> RFC-007 D3: zero-key backfill marks registry_complete=True while Postgres stays empty, making entire corpus invisible to all five MCP query tools.
c. RFC-006 D3 -> RFC-007 D2: Postgres registry delete scheduled via fire-and-forget, violating HR2 -- if task failed, delete_doc logged full cascade success while registry row persisted.
d. RFC-006 D2 -> RFC-008 D1: registry_complete checks created per-call Redis connections, causing connection storms.
e. Runs 15-16: persistence-timing race between worker write and scorer read -- cabinet_resolution_no_96 ERRORed (Run 15), recurred identically for a different doc (Run 16).
f. Run 19: RFC-034 D18 write-barrier refactoring introduced timing regression -- SLA document completed minutes after rest of corpus, ERROR at score time.
g. Runs 11-12: artifact persistence regression for human rights doc (NoSuchKey despite prior valid artifact).
h. Postprocess registry latency audit: dual-write not atomic with MinIO write, on exception swallows and job still reports success.

#### Code Evidence
storage/documents.py:141-343 delete_doc: 203 lines, complexity 49 (noqa C901, PLR0915, ruff-grandfathered), 7+ cascade steps with store-specific error handling (VERIFIED). worker/registry_mirror.py:55-135 _upsert_registry_row: reads MinIO artifact via asyncio.to_thread(read_registry_fields), overlays verdict_fields, CAS-upserts to Postgres -- best-effort, never fails the job (VERIFIED). registry/queries.py:19-91 _UPSERT_SQL: ON CONFLICT DO UPDATE with processed_at CAS guard and verdict-priority CAS guard (VERIFIED). worker/job.py:95-405 process_document_job: 311 lines, complexity 21, owns staging cleanup as the ONLY code path that can remove uploads/staging/<job_id>/ objects (VERIFIED).

#### Key Files
- src/pageindex_mcp/storage/documents.py
- src/pageindex_mcp/worker/registry_mirror.py
- src/pageindex_mcp/registry/queries.py
- src/pageindex_mcp/worker/job.py
- src/pageindex_mcp/storage/hash_cache.py

#### Simplification Proposal
**(1) Core Simplification**

Replace the current fan-out dual-write (child writes MinIO, parent re-reads MinIO, parent upserts Postgres, reconcile later heals drift) with a single write-result contract: the converter child returns ALL registry-relevant fields in its stdout JSON, so the parent upserts Postgres from that payload directly -- never re-reading MinIO. This eliminates the persistence-timing race by construction. Separately, decompose `delete_doc`'s 203-line monolith into a declarative erasure manifest (a list of `(store, key_pattern, required)` tuples) iterated by a single loop.

**(2) Concrete Restructuring Steps**

Step A -- Eliminate the MinIO re-read in the dual-write path (~-30 lines net): `src/pageindex_mcp/worker/job.py` expands the child's JSON return to carry all `_REGISTRY_FIELDS`; `src/pageindex_mcp/worker/registry_mirror.py` removes `asyncio.to_thread(read_registry_fields, ...)` and accepts `registry_fields: dict` directly; `src/pageindex_mcp/storage/verdict.py`'s `read_registry_fields` stays for reconcile-only use.

Step B -- Declarative erasure manifest in delete_doc (~-80 lines net): `src/pageindex_mcp/storage/documents.py` replaces the 7+ inline try/except blocks with a list of `ErasureStep(name, store_type, key_fn, required)` iterated by one loop; add a `staging_by_job_id` step (closing the job_id-keyed staging gap) and a `legacy_hash_blob` step (closing the HR2 hash-cache leak).

Step C -- Job-to-doc linkage for staging cleanup (~+25 lines net): `src/pageindex_mcp/worker/job.py` writes a Redis mapping `pageindex:job_doc:{job_id} -> doc_id` with TTL matching JOB_TTL; `src/pageindex_mcp/storage/documents.py` adds `_staging_keys_for_doc(doc_id)` via reverse lookup.

Step D -- Fix stale-row age guard for partial-data rows (~+5 lines net): `src/pageindex_mcp/registry_backfill/cleanup.py` changes empty-processed_at treatment from "old enough to delete" to "age-protected"; add a separate config-gated sweep for truly old rows.

Step E -- Legacy hash blob purge (~+15 lines): `src/pageindex_mcp/storage/hash_cache.py` adds `hash_cache_delete_legacy(filename)`, wired into the Step B erasure manifest.

**(3) Historical Bug Classes Prevented**

Persistence-timing race (Runs 15-16, Run 19): eliminated by construction -- parent never re-reads MinIO for the dual-write. Partial-data row deletion by reconcile: fixed by inverting the empty-processed_at default. HR2 hash-cache leak (RFC-002 Amendment 2): legacy MinIO blob entry purged by the erasure manifest. Staging objects surviving erasure: job-to-doc Redis mapping makes staging reachable from delete_doc. Fire-and-forget registry delete (RFC-006 D3 -> RFC-007 D2): declarative manifest with required:bool makes each step's criticality explicit and auditable. Future cascade omissions: adding a new derived store becomes a one-line manifest entry instead of a 20-line inline block.

**(4) Migration Risk and Sequencing**

Five independent PRs: 1) Step A first (highest value, lowest risk -- falls back to existing MinIO-read path if new fields absent, zero behavioral change for reconcile). 2) Step D second (small, high-value safety fix; risk: truly stale legacy rows linger longer, mitigated by the config-gated sweep). 3) Step B third (pure refactor; risk: manifest ordering must match current order -- Redis before MinIO before Postgres -- mitigated by a golden-sequence test). 4) Step C fourth (additive; missing mapping treated as "no staging to clean", idempotent). 5) Step E last (best-effort; risk of concurrent read-modify-write on the legacy JSON blob, mitigated by conditional PUT or accepted as last-writer-wins on a shrinking legacy store).

**(5) Estimated Effort**

Step A: 1-2 days. Step B: 2-3 days. Step C: 0.5 day. Step D: 0.5 day. Step E: 0.5 day. Total: 5-7 days across 5 independent PRs over 2-3 weeks.

---

### Zone 5: God-Function Orchestration with Duplicated Divergent Logic
**Severity:** high | **Bug count:** 10

#### Mechanism
When a god-function absorbs a new concern (e.g., RFC-034 D18 write-barrier added to storage.py, RFC-036 D0 landscape rasterize/rotate added to converters.py), it is implemented inline as another branch in the existing if/elif chain. A fix inside the function interacts with every other branch because they share mutable local state. Duplicated logic (different table-separator regexes, different constant definitions) means a fix to one copy does not propagate to the other by construction. The table-separator duplication specifically means an all-dash DATA row satisfying both predicates is treated as a header separator, silently truncating row collection at ~7 rows with remainder spilling into prose blocks. The timeout chain interaction (CHILD_TIMEOUT 3600s x 16.5 = 59400s, capped to MAX_EFFECTIVE_TIMEOUT 54000s) means the cap undercuts the calibrated worst-case budget by ~9%, and a raw TimeoutError from the cap is NOT routed through the child-error registry's terminal-reason gate, causing ~30h of retries before DLQ.

#### History
a. RFC-002 Amendment 1: five dag.yaml module-boundary edges didn't match actual imports -- spec-vs-code drift across all module boundaries.
b. RFC-029 D3: naive fence-parity toggle + unconditional fence/HR-marker stripping destroyed content across corpus -- SLA 264 blocks->0, MOU 89% loss, marsoom-13 0 nodes/0 chars, Reitlehrer persistent 32% char reduction masked across runs.
c. RFC-035: table-meta/chart-block segmentation refactor broke BOTH landscape AND portrait orientations together (Doc 14 MARGINAL->FAIL with 71 kv-singleton fragments, Doc 15 PASS->MARGINAL with 89% singleton fragmentation).
d. RFC-036 D0: uncapped landscape rasterize caused serial 300-DPI OCR re-runs; chart axis labels shattered into 71+ singleton kv blocks by _segment_table_nodes.
e. RFC-036 D1: RFC-034 D18 write-barrier added up to 4.4s blocking delay per save call, unhandled exception caused arq job retries.
f. RFC-036 D2: RFC-034 D19 enrichment displacement fix was fully implemented and staged in git but never committed -- inactive during Run 19.
g. RFC-028 D0: chunked_docling_timeout_s() function created but never imported or called by worker.py; world-stats-pocketbook kept timing out across Runs 9-11.
h. Run 13: body-extraction silently returned empty (0 nodes/0 chars) and persisted flat.json anyway, violating Hard Rule 5.

#### Code Evidence
worker/subprocess_mgr.py:79-263 _run_converter_subprocess: complexity 31, 185 lines, noqa C901/PLR0915, fuses spawn+handshake parsing+3-RFCs'-worth of timeout policy+OOM/error classification (VERIFIED). worker/subprocess_mgr.py:191-197: effective_timeout > MAX_EFFECTIVE_TIMEOUT logged as warning only, silently capped (VERIFIED). worker/job.py:95-405 process_document_job: 311 lines, complexity 21, owns deadline math + memory admission + subprocess invocation + 3 exception-class handlers + registry upsert + staging cleanup (VERIFIED). storage/documents.py:141-343 delete_doc: 203 lines, complexity 49, 7+ store-specific deletion strategies inlined (VERIFIED).

#### Key Files
- src/pageindex_mcp/worker/subprocess_mgr.py
- src/pageindex_mcp/worker/job.py
- src/pageindex_mcp/storage/documents.py
- src/pageindex_mcp/helpers/flat.py
- src/pageindex_mcp/helpers/tree_split.py
- src/pageindex_mcp/helpers/tables.py

#### Simplification Proposal
**(1) Core Simplification**

Extract three single-owner modules: (a) `src/pageindex_mcp/helpers/pipe_table.py` owning ALL pipe-table detection predicates (is_pipe_row, is_separator_row) -- both `src/pageindex_mcp/helpers/tables.py` and `src/pageindex_mcp/helpers/tree_split.py` import from it; (b) delete the `_job_key` duplicate in `src/pageindex_mcp/cache.py` and import from `src/pageindex_mcp/job_status.py`; (c) extract the timeout-policy chain in `src/pageindex_mcp/worker/subprocess_mgr.py` into a pure function `compute_effective_timeout(handshake, child_timeout, max_timeout) -> float` in `src/pageindex_mcp/worker/constants.py`, making the cap-vs-multiplier interaction unit-testable and ensuring the cap produces a `ConverterChildError(reason="converter_timeout")` instead of a raw `TimeoutError`. For `delete_doc`, extract each numbered erasure step into a typed list of `ErasureStep` callables iterated by a 15-line driver loop.

**(2) Concrete Restructuring Steps**

Step A -- Unify table separator detection: new `src/pageindex_mcp/helpers/pipe_table.py` (~35 lines) with one regex and one predicate; `src/pageindex_mcp/helpers/tables.py` and `src/pageindex_mcp/helpers/tree_split.py` delete their divergent copies and import from it. Net: ~0 (one predicate instead of three).

Step B -- Deduplicate `_job_key` and `_IMAGE_EXTS`: `src/pageindex_mcp/cache.py` imports from `src/pageindex_mcp/job_status.py` (-3 lines); `src/pageindex_mcp/client/images.py` imports `_IMAGE_EXTS` from `src/pageindex_mcp/client/indexer.py` (-1 line).

Step C -- Extract timeout policy into a pure function: `src/pageindex_mcp/worker/constants.py` gets `compute_effective_timeout(...)` (~30 lines) encapsulating chunked-Docling dynamic timeout, 16.5x inspector multiplier, and MAX_EFFECTIVE_TIMEOUT cap; `src/pageindex_mcp/worker/subprocess_mgr.py` replaces 47 lines of inline policy with one call (-35 lines), dropping complexity from 31 to ~20.

Step D -- Route timeout-cap through the error registry: when `compute_effective_timeout` returns `capped=True` and the asyncio.timeout fires, raise `ConverterChildError(error_class="EffectiveTimeoutCapped")` instead of bare TimeoutError; add the class to `errors.py`'s `_CHILD_ERROR_REGISTRY` with `reason="converter_timeout", terminal=True`. ~+8 lines.

Step E -- Decompose `delete_doc` into a step list: `ErasureStep = Callable[[str, MinioClient, list[str]], None]`; each of the 7 numbered steps becomes a standalone ~15-25 line function; a ~15-line driver loop iterates and collects errors. Complexity drops from 49 to ~5 per function plus ~8 for the driver.

Step F -- Slim `process_document_job`: extract `_persist_effective_timeout` closure to a module-level function (-15 lines); extract the three parallel error handlers into a single `_handle_converter_error(...)` dispatcher (-50 lines), dropping complexity from 21 to ~12.

**(3) Historical Bug Classes Prevented**

Table separator divergence (Fix-2/Fix-4 ~7-row truncation): Step A eliminates the root cause -- one predicate, one definition to fix. RFC-036 D0 (timeout cap causing 30h retries before DLQ): Steps C+D route capped timeouts through the terminal-reason gate. RFC-028 D0 (chunked_docling_timeout_s created but never called): Step C's pure function has an explicit unit-test contract, catching unwired timeout sources immediately. RFC-036 D1 (write-barrier exception causing arq retries): Step F's unified error handler covers all exception types through a single dispatch point. RFC-002 Amendment 1 (spec-vs-code drift): Steps A and B reduce duplicate definitions from 2-3 to 1, eliminating drift targets.

**(4) Migration Risk and Sequencing**

Low-to-moderate risk -- every step is extract-plus-redirect with no behavioral change. Sequence: 1) Step A first (lowest risk, highest value, unit tests for unified predicates before changing callers). 2) Step B same PR as A (trivial). 3) Step C (medium risk -- load-bearing timeout chain; mitigated by exact numeric unit test assertions). 4) Step D same PR as C. 5) Step E independent of A-D (risk: erasure cascade order is load-bearing per HR2; mitigated by a test asserting step list matches the documented cascade order). 6) Step F depends on C+D merged (medium risk touching the arq job handler; mitigated by existing OOM/timeout/child-error/success integration tests).

**(5) Estimated Effort**

Step A: 2h. Step B: 0.5h. Steps C+D: 4h. Step E: 4h. Step F: 3h. Total: ~13.5h, roughly two developer-days.

---

### Zone 6: validate_tree Reason-String Dispatch
**Severity:** high | **Bug count:** 9

#### Mechanism
When a new quality check is added to validate_tree's GATE_TABLE (e.g., suspect_density, low_content_density, empty_node_contamination, arabic_low_content_ratio), the primary defect and reason string change. But client.py's recovery routing only handles a specific set of known reasons. An unhandled reason falls through the if/elif chain to raise LowQualityTreeError -- a terminal ERROR with no artifacts saved, even when classify_verdict intended the reason to produce a FAIL with a persisted artifact. The early-exit ordering compounds this: validate_tree evaluates all 10 gates exhaustively (VERIFIED), but the primary defect is the first firing gate in table order. If a structural gate (node_count<3, depth<2) fires alongside garbling, the structural defect becomes primary, and OCR escalation (which requires reason in {garbling, node_garbling}) never triggers. This ordering dependency generates a recurring pattern: detection lands in the gate table but the recovery path requires a specific reason string that the gate table's priority order does not produce.

#### History
a. RFC-003 -> RFC-004 Amendment 1: validate_tree conflated flat documents with garbled documents (both raised low_quality_tree), corrected by narrowing terminal to garbling only.
b. RFC-016 D5: VLM block fired only on reason=='garbling', missing cases where validate_tree rejected with reason=='node_count<3' -- Arabic watermark producing shallow tree skipped VLM entirely.
c. RFC-023 D11: Fix-3 OCR escalation only fires on reason=='garbling'; when content-stripped markdown produces a tree failing with node_count<3 or depth<2, page-level OCR retry never fires.
d. RFC-029 D0: Arabic Presentation-Forms detection added reason='garbling' to validate_tree, but reason=garbling is explicitly excluded from flat routing -- LowQualityTreeError raised with NO artifacts saved.
e. RFC-030 D2: four new validate_tree reasons unhandled by client.py -- fell through to raise LowQualityTreeError, causing 3 PASS->ERROR regressions (Penal Code, federal_decree_law_no_33, marsoom 33).
f. RFC-036 D3: rtl_reversal routed to terminal rejection with no flat-fallback attempt.
g. Observation #5330: early-exit in validate_tree before garble check makes OCR escalation unreachable for docs classified as node_count<3.

#### Code Evidence
helpers/tree_validation.py:235-308 validate_tree: runs all 10 GATE_TABLE entries exhaustively, primary defect = first firing gate in table order; returns TreeGateResult with fully silent OK when nothing fires (VERIFIED). helpers/gates.py:39-47 Gate1 (_gate_garbling): hard_fail=True, severity highest in table order. helpers/gates.py:72-96 Gate4 (_gate_node_garbling): severity=3. helpers/tree_validation.py:296-304: fired list built in GATE_TABLE order, primary_defect = fired[0] (VERIFIED). helpers/verdict.py:118-216 evaluate_gates: checks defect in HARD_FAIL_DEFECTS for immediate FAIL (VERIFIED).

#### Key Files
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/client/recovery.py

#### Simplification Proposal
No dedicated simplification proposal was generated for this zone in this audit pass; see the Verdict Promotion / Quality Gate Stack proposal's note on gate-registration completeness (a GATES registry with exhaustive assertions) as the adjacent fix already addressing part of this zone's dispatch-completeness problem.

---

### Zone 7: Config Layering Split and Dead-Code Accumulation
**Severity:** medium | **Bug count:** 7

#### Mechanism
When a feature is designed, its config flag is added to PipelineConfig and its implementation code is written. But the flag may be frozen at import time (not refreshable), and the actual routing decision may bypass PipelineConfig entirely (PDF_CONVERTER reads os.getenv directly). The result is that the config-singleton abstraction provides observability (effective_config_snapshot) but not control -- the real decision uses a different path. Test fixtures that monkeypatch env vars and call reset_pipeline_config() silently get stale process-start values for the 6 frozen flags, with no error or warning. Production is accidentally safe because the converter child process re-imports config.py from a fresh os.environ, but in-process consumers (compute_verdict, tree-quality gates) get the stale values. The dead-code pattern compounds this: a feature is fully implemented but never wired into the call chain that would activate it, and the task tracking marks it complete based on the implementation, not the wiring.

#### History
a. RFC-027 D7 -> RFC-028 D0: chunked_docling_timeout_s() function created but never imported or called by worker.py -- world-stats-pocketbook kept timing out across Runs 9-11 despite the task being marked complete.
b. RFC-031 D4 -> RFC-032: PDF_INSPECTOR_PRECLASSIFY config flag added as 'toggle for future promotion' but was dead code -- entire shadow-classification pipeline computed and logged but never consumed by index() for routing decisions.
c. RFC-030 D5: _check_bidi_coherence fully implemented at helpers.py lines 936 and 1028 (duplicate definition) but never wired into validate_tree or any pipeline function.
d. RFC-029 D6: Phase B judge-calibration rules specified and task marked complete, but rules never written to SKILL.md.
e. GarbleConfig.from_config() hardcodes garble_digit_floor=500 instead of reading cfg.garble_digit_floor -- bypasses the config consolidation it claims to implement.
f. effective_config_snapshot() can persist stale allow_agpl_fallback value, tainting the audit trail for Hard Rule 4 AGPL awareness.

#### Code Evidence
config.py:22-61 six module-level constants frozen at import: PDF_INSPECTOR_PRECLASSIFY, ALLOW_AGPL_FALLBACK, REMOTE_MD_RENORMALIZE, OCR_ESCALATION_GARBLE, OCR_ESCALATION_PER_PICTURE, IMAGE_DOMINANT_OCR_ESCALATION_ENABLED (VERIFIED). config.py:433-444 PipelineConfig.from_env(): uses PDF_INSPECTOR_PRECLASSIFY (frozen), ALLOW_AGPL_FALLBACK (frozen), etc. -- copies frozen values rather than re-reading env (VERIFIED). config.py:514-541 reset_pipeline_config(): calls PipelineConfig.from_env() which copies frozen constants, pushes into 6 re-importer submodules -- docstring claims 're-read env vars' but 6 fields are never re-read (VERIFIED). converters/pipeline.py pdf_markdown_converters: reads os.getenv('PDF_CONVERTER','docling') live, bypassing pipeline_config.pdf_converter (in_degree 0 for routing).

#### Key Files
- src/pageindex_mcp/config.py
- src/pageindex_mcp/converters/pipeline.py
- src/pageindex_mcp/helpers/garble.py

#### Simplification Proposal
No dedicated simplification proposal was generated for this zone in this audit pass. Step A of the Garble Detection Fragmentation proposal (fixing `garble_digit_floor=500` to read `cfg.garble_digit_floor`) directly addresses one of this zone's contributing bugs.

## Cross-Cutting Themes
- Specification-to-implementation drift is the single most recurring failure class: RFC-000's frozen architectural decisions (PDF routing, quality gates, module boundaries, job lifecycle, HR3 worker gate, PDF-Inspector classification) repeatedly turn out to have been designed but never wired into the code that actually runs, discovered only by later RFCs or audits.
- Hard Rule violations recur across the project's life: HR2 (erasure cascade) and HR5 (never silently persist a low-quality tree) are each violated multiple times independently — reversed cascade order, fire-and-forget registry deletes, unhandled new validate_tree reasons falling through to raise, and promotion paths (image_enrichment_promoted, cat_b_promoted) that let near-zero-content documents PASS.
- Garble/quality-gate logic is chronically brittle and duplicated: the same detection primitives (digit-ratio floor, token-repetition guard, Latin-gibberish check, BiDi/RTL coherence) are reimplemented in multiple functions without a shared abstraction, causing fixes landed in one place to leave the other unpatched, and encoding-range mismatches (canonical vs presentation-forms Arabic) make some checks structurally unable to ever fire.
- Fixes narrowly designed for one document class routinely regress an adjacent class: page-coverage/clip-text filters built to skip decorative full-page backgrounds also block genuine scanned pages; forced-OCR meant to rescue garbled Arabic PDFs strips PictureItems and collapses trees to flat; improved OCR language detection dilutes garble signal on junk documents; RFC-035's chart-segmentation fix for landscape pages broke portrait charts too.
- Detection landing without an escalation/recovery path is a repeated pattern: RFC-025's garble detection correctly flags garbling but never triggers OCR escalation; RFC-029 D2's Presentation-Forms detection has no non-'garbling' fallback route, turning a recoverable FAIL into a terminal ERROR with zero artifacts.
- Verdict oscillation and judge/gate divergence mask persistent structural defects: the same document flips PASS/MARGINAL/FAIL/ERROR across many consecutive runs (وارد 597, MOU, مرسوم 13/33, Penal Code, world-stats-pocketbook) as gate-hardening changes land only in the audit/scoring layer rather than the persisted gate, or as judge-severity recalibration substitutes for actual root-cause fixes.
- Metadata/content divergence hides real data loss: recovered text increments meta counters (flat_char_count) without landing in persisted blocks (15x divergence observed), and synthetic structures/empty structure=[] inputs produce degenerate metrics that either vacuously pass or vacuously fail downstream gates.
- Concurrency and consistency gaps between independently-written stores (MinIO vs Postgres registry, worker write vs scorer read) cause silent partial failures — non-atomic dual-writes, fire-and-forget deletes, and persistence-timing races that recur identically across multiple runs until a write-visibility barrier is added.
- Parameter-threading failures silently disable defenses that unit tests never exercise: expected_script/had_presentation_forms not passed to garble-check callers, ocr_lang_override not passed to the forced-OCR escalation path, and thread-local state that doesn't cross an asyncio.to_thread boundary, each making a fully-implemented feature a no-op in production.
- Deferred and dead-code patterns are common: capability code (timeout calculators, BiDi coherence checks, PDF-Inspector classification, config toggles) is frequently built end-to-end but left unwired for one or more RFC cycles, requiring a dedicated follow-up RFC purely to activate what already exists.
- Threshold/calibration changes swing between over- and under-rejection: density gates calibrated against a single problem document reject whole classes of legitimate legal trees, while leaf-ratio and garble-ratio threshold relaxations let genuinely garbled content pass — these tuning passes rarely account for the full corpus distribution.
</content>
