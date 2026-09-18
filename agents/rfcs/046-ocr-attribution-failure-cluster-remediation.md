---
id: RFC-046
title: OCR Attribution & Failure-Cluster Remediation
type: rfc
status: draft
date: 2026-09-12
plan-impact: no
tags:
  - rfc
  - ocr-attribution
  - garble-detection
  - verdict-plumbing
  - failure-clusters
  - pre-surya
aliases:
  - RFC-046
  - OCR Attribution & Cluster Remediation
governs:
  - "[[design-rfc046-ocr-attribution-failure-cluster-remediation]]"
  - "[[tasks-rfc046-ocr-attribution-failure-cluster-remediation]]"
supersedes: []
---
## Context

Corpus Run 8 (`audit/CORPUS_REINGESTION_AUDIT_RUN-8.md`) closed all five ERROR documents via the RFC-045 fix chain but left five FAIL verdicts. The obvious reading — Tesseract's recognition quality is the bottleneck, so add a better OCR engine — motivated a four-engine spike (`agents/spikes/ocr_eval_rfc046/`) and a pre-RFC plan proposing Surya OCR v2 as a quality fallback tier.

**Tracing every failing document to the line that condemns it refutes that reading.** The five failures are five instances of six clusters, and only one cluster is an OCR-quality problem at all — and even that one is a configuration defect before it is a quality defect:

| Cluster | Mechanism | Docs |
|---|---|---|
| **C1** density floor | Flat 1500 chars/page floor, hard-fail, `recovery_waived`; numerator excludes node `summary` and image `ocr_text` | 6, 18, (15) |
| **C2** OCR language selection | `detect_ocr_langs(filename)` only for standalone images; no content probe; every recovery rung `.pdf`-gated | 13 |
| **C3** flat verdict on tree signals | `flat_structure` is a dead argument; image blocks invisible under `CHAR_COUNT` | 14, (15), (9) |
| **C4** OCR gated off, then discarded | `PRE_GARBLE_FORCE_OCR_ENABLED=false`; keep-best arbitrates on post-LLM tree text, not the recovered markdown | 17, (6) |
| **C5** presentation-forms as proof of garble | `any()` single-codepoint detector threaded into a no-ratio garble prong; survives NFKC | 22, (19, 21, 23 historically) |
| **C6** garble-primary masks the flat lifeboat | Garble force-promoted to primary → `decide_route(GARBLING) = TREE` | 17, 22, 13 |

Three structural facts make an engine-first approach untenable right now:

1. **On the default configuration, Docling performs no OCR at all on the primary conversion pass.** `converters/docling_conv.py:80` computes `do_ocr = force_ocr or DOCLING_DO_OCR` (default `"0"`), and both inputs to `force_full_page` are dark — `PRE_GARBLE_FORCE_OCR_ENABLED=false` (`config.py:505`) and `PDF_INSPECTOR_PRECLASSIFY=False` (`config.py:42,486`). The line binding Tesseract (`docling_conv.py:98`) is never reached. Every byte of OCR comes from a recovery rung or per-picture enrichment. There is no primary tier to "fall back" from.

2. **No engine identity exists anywhere.** `grep -rn "ocr_engine\|OCR_ENGINE" src/` returns nothing; `state.used_converter = "docling"` is hardcoded (`recovery.py:341`). We cannot attribute a verdict to an engine, so we cannot measure whether any engine change helped.

3. **The evaluation evidence is not reproducible from the repository.** All three committed artifacts in `agents/spikes/ocr_eval_rfc046/` contain Tesseract data only (84,733 chars, 25/25 docs; Surya/VL/Paddle 0 chars, 0/25 docs), truncated to three pages per document, against the 296,088 chars the accompanying report attributes to Tesseract. The report cites raw data at `/tmp/ocr_eval_4engine/`, which no longer exists. Under Hard Rule #1 this cannot ground an RFC.

This RFC therefore scopes **P0 (baseline truth) and P0.5 (cluster remediation)** from `[[plan-rfc046-surya-quality-fallback]]`. It contains **no OCR-engine code**. A second engine is deferred to a successor RFC, to be written only after a corpus run measures what survives this one.

### Relationship to Prior RFCs

- **RFC-045** fixed the Arabic garble chain but its presentation-forms fix is incomplete: commit `2c39168` removed the NFKC fallback *inside* `detect_garble` (fixing Docs 19/21) and never touched the threaded `RtlDecision` flag, which is strictly more sensitive. D5 completes it.
- **RFC-044** documented the `force_full_page` → `decide_ocr_strategy` authority inversion and deferred consolidation to "Phase B." This RFC does **not** attempt Phase B; it adds attribution without restructuring authority.
- **RFC-042** is open on §3 (`PROMOTION_ORDER`) and task 4.2 (config consistency property test). D9 adopts 4.2 into this RFC's scope because D2 adds config surface (see Open Questions OQ1).
- **RFC-041** task 3.5a (RFC-037 Release B full-corpus verdict-diff gate) owns the corpus baseline this RFC's gate depends on.
- **RFC-021 QF1** established the pre-garble OCR deferral. D8 corrects its flag's parse asymmetry without changing its default or its doctrine.
- **RFC-036 D7** closed a PaddleOCR/EasyOCR spike negative — but every call in that spike was "Connection refused"; the services never started. That negative is void, and the report records an explicit reopen trigger. Relevant to the successor RFC, not this one.

### A note on numbering

`agents/spikes/ocr_eval_rfc046/` was named before this scope split. RFC-046 is this remediation RFC; **the OCR-engine question becomes RFC-047.** The spike directory is left at its committed path rather than renamed.

## Goals

1. Make every stored verdict attributable — to the engine that produced the text and the garble prong that condemned it.
2. Restore a reproducible evidence base for any future engine comparison.
3. Fix the six failure clusters at their traced root causes, without touching verdict thresholds.
4. Establish, by corpus measurement, which failures survive — so the engine question is decided on evidence rather than on an unreproducible spike.

## Non-Goals

1. **Introducing any OCR engine.** No Surya, Paddle, VL, or engine-selection code. Deferred to RFC-047.
2. **Threshold changes.** No adjustment to `RFC029_MIN_SCANNED_DENSITY_FLOOR`, `PASS_MAX_LEAF_RATIO`, `hard_fail_max_leaf_ratio`, or any `VerdictThresholds` value. Threshold widening is a documented systemic anti-pattern (`audit/zones/_index.md`); cross-session history records five consecutive RFCs each fixing and re-breaking this boundary.
3. **RFC-044 Phase B/C authority consolidation.** `decide_ocr_strategy` keeps exactly one call site.
4. **Flipping `PRE_GARBLE_FORCE_OCR_ENABLED` to `true`.** D8 fixes how it parses, not what it defaults to.
5. **Docling capability gaps.** Doc 14's underlying weakness (vector charts never classified as `PictureItem`) is a Docling limitation; this RFC fixes only the verdict plumbing around it.
6. **Rewriting the converter chain.**

## Glossary

- **Cluster (C1–C6):** a shared root-cause mechanism behind one or more failing documents, as traced in Context.
- **Threaded PF flag:** `RtlDecision.had_presentation_forms`, set by the bidi renormalizers and carried into garble detection — distinct from the ratio-based presentation-form scans in `garble.py:47` and `tree_validation.py:310`.
- **Recovered markdown:** the markdown `_execute_ocr_retry` obtains from an OCR pass, *before* `_reconvert_and_revalidate` rebuilds a tree from it.
- **Attribution:** the pair (engine, fired prongs) recorded in the sidecar for each persisted verdict.
- **P0 / P0.5:** the first two phases of `[[plan-rfc046-surya-quality-fallback]]` §6 — baseline truth, and cluster remediation.

## Requirements

### Requirement 1: Reproducible OCR Evaluation Evidence

**User Story:** As an RFC author, I want every quantitative OCR claim to regenerate from artifacts committed in this repository, so that no decision rests on data that cannot be re-derived.

#### Acceptance Criteria

1. `scripts/ocr_spike_eval.py` SHALL derive OCR languages through `converters.ocr_langs.detect_ocr_langs` and `ensure_tessdata` rather than its private map at `:234`, so the harness measures the production language path.
2. `compare_results` (`:553`) SHALL emit engine-correct output keys; the `comparison_surya` block SHALL NOT carry `paddleocr_*` key names (currently consumed at `:691`).
3. The harness SHALL fail loudly, with a non-zero exit, when a configured engine endpoint is unreachable — a connection failure SHALL NOT be recorded as a zero-character result. *(This is the defect that produced the void RFC-036 D7 negative.)*
4. A re-run SHALL produce committed artifacts in which every engine that was enabled has non-zero data for every document it processed, at full page count (no 10-page cap), with the per-engine totals in the accompanying report regenerated from those artifacts.
5. The report SHALL record engine versions, host, and run date, and SHALL state explicitly that it measures character yield, not accuracy, absent ground truth.
6. `agents/spikes/ocr_eval_rfc046/eval_report.md` SHALL NOT be cited as evidence by any artifact until criteria 4 and 5 hold. Until then it SHALL carry a header marking its numbers unverified.

### Requirement 2: End-to-End OCR Attribution

**User Story:** As an auditor comparing two corpus runs, I want each stored verdict to name the engine that produced its text and the garble prong that condemned it, so that a verdict change can be attributed to a cause rather than guessed at.

#### Acceptance Criteria

1. An `OcrEngine` `StrEnum` SHALL be introduced with at least the member `TESSERACT`, in the module that owns OCR decision types.
2. `OcrDecision` (`picture_plane.py:35`) SHALL carry an `engine: OcrEngine` field. `decide_ocr_strategy` SHALL continue to have **exactly one call site in `src/`, in `converters/pictures.py`** — `tests/test_architecture_guards.py:1089-1114` SHALL continue to pass unmodified.
3. Every OCR invocation site SHALL record the engine that served it. The enumerated sites are: `converters/pictures.py:208` (`_tesseract_ocr_image`, the chokepoint for `pictures.py:657`, `pictures.py:892`, `formats.py:371`, `indexer.py:915`), `converters/formats.py:339` (`tesseract_ocr_pdf_pages`), `converters/docling_conv.py:98` (Docling-mediated), `client/recovery.py:725-738` (VLM raster last resort), and **`converters/pipeline.py:376` (`_landscape_rasterize_rotate_reextract`)**.
4. `state.used_converter` SHALL NOT be assigned a hardcoded literal in `recovery.py:341`; it SHALL reflect the converter actually used.
5. `GarbleReport.fired_prongs` (`garble.py:533-535`) SHALL be persisted to the sidecar for every document that reaches a garble verdict. `_persist_tree_result` (`indexer.py:1322-1323`) currently writes only `all_defects`.
6. `_persist_flat_result` SHALL persist the same attribution fields as `_persist_tree_result`.
7. The attribution fields SHALL appear in `effective_config_snapshot()`'s `_SIDECAR_FIELDS` where they are configuration, and in the per-document sidecar where they are observation.
8. An architecture guard test SHALL assert that no OCR invocation site enumerated in criterion 3 persists a verdict without an engine label.
9. `OCR_ESCALATION_TOTAL` SHALL carry an engine label.

### Requirement 3: Corrected Run-8 Baseline

**User Story:** As anyone grading a future corpus run, I want the Run-8 baseline to state the correct tally and provenance, so that projections are measured against a true starting point.

#### Acceptance Criteria

1. The Run-8 summary tally SHALL be recounted from the per-document scorecard rows and corrected if it disagrees. The pre-RFC plan's recount gives 13 PASS / 6 MARGINAL / 6 FAIL against the stated 14 / 6 / 5, with Doc 18 (`suspect_density` 1,413 chars/page) as the omitted failure; this SHALL be confirmed before amendment, not assumed.
2. `audit/CORPUS_REINGESTION_AUDIT_RUN-8.md:5` SHALL record the branch the run was actually performed on. It currently reads `ICR-97-rfc44-recovery-dispatch-wiring`.
3. The correction SHALL be recorded as a dated addendum, not a silent edit, consistent with the RFC-025 D4 precedent.
4. **Documentation-only (2026-09-15).** Run-8's stored artifacts are not recoverable — it claims 20 meta objects at `processed_at` 2026-09-09; the bucket holds 17, newest 2026-08-07, at an unchanged endpoint. The tally SHALL therefore be recounted from the audit's own scorecard rows and **SHALL NOT** be re-derived from MinIO. Every gate anchors to the fresh attributed baseline of Requirement 9, not to Run 8.

### Requirement 4: Content-Derived OCR Language Selection (C2)

**User Story:** As a document whose filename is in a different script from its contents, I want my OCR languages chosen from what is actually on the page, so that I am not transliterated into gibberish by the wrong tessdata.

#### Acceptance Criteria

1. The standalone-image path (`indexer.py:893`) SHALL NOT derive OCR languages from the filename alone.

> **Prior art (2026-06-30).** `issue/data2_fixes_validation_report.md` recommendation #4 reads: *"Wire the Tesseract route for image input (currently dead — Docling handles images but the OCR path Fix-5 gates never fires)."* That is this requirement, recommended five months earlier and never implemented. The same report records the class on a **PDF** as well (Doc 22, *"eng-only OCR over Arabic script"*), so wrong-language OCR is **not image-only**. This deliverable scopes the image path; whether the PDF paths need the same treatment SHALL be decided from the Wave 1 baseline's persisted `fired_prongs`, not assumed.
2. After a first OCR pass on an image, the output text SHALL be re-examined with `detect_ocr_langs`. When the detected script set is not covered by the languages used, a second OCR pass SHALL run with the corrected set, and its output SHALL be used when it is not garbled.
3. The re-examination SHALL be bounded to at most one corrective re-OCR per document.
4. Garble recovery SHALL be reachable for image inputs. `_recover_garble_ocr` (`recovery.py:437`) and `_recover_vlm_fallback` (`recovery.py:662`) currently return immediately for any `ext != ".pdf"`; the eligibility condition SHALL be widened to cover `_IMAGE_EXTS`, or an image-specific recovery rung SHALL be added to `GATES`.
5. Any new recovery method SHALL carry the `full_page_already_applied` guard that `tests/test_architecture_guards.py:1063` requires, and SHALL use `_all_defects(state)` per the `TestEligibilityPredicateSymmetry` contract (`:1134`).
6. A unit test SHALL verify: an image whose filename is pure Latin and whose content is ≥30% Arabic is OCR'd with an Arabic-capable language set on the second pass.
7. A unit test SHALL verify Doc 13's failure mode specifically: filename-derived `["eng"]` on Arabic content no longer terminates in a persisted `garbling` verdict without a corrective pass having been attempted.

### Requirement 5: Presentation-Forms Detector Alignment (C5)

**User Story:** As a clean Arabic document containing a single common ligature, I want not to be condemned as garbled by a detector that fires on one codepoint.

#### Acceptance Criteria

1. `indexer.py:190` and `converters/normalize.py:159` SHALL set `had_presentation_forms` using the same ratio predicate every other presentation-form detector uses — `ar_count > 0 and (pf_count / ar_count) > 0.50`, matching `garble.py:47` and `tree_validation.py:310` — instead of `any(...)`.
2. The ratio threshold SHALL be defined once and shared by all four detectors; it SHALL NOT be a fourth independent literal.
3. The NFKC normalization currently triggered by the `any(...)` result SHALL continue to run whenever presentation forms are present at all, so normalization coverage does not regress when the *signalling* threshold rises. Detection sensitivity and normalization triggering SHALL be separated.
4. A unit test SHALL verify: a document containing one ﷲ or ﷺ among otherwise unshaped Arabic does not set `had_presentation_forms`, and is still NFKC-normalized.
5. A unit test SHALL verify: a document genuinely dominated by presentation forms (>50% of Arabic characters) still sets the flag and still fires the `presentation_forms` prong.
6. A regression test SHALL verify Docs 19, 21 and 23 — fixed by `2c39168` through the other path — remain unaffected.
7. Doc 22's verdict SHALL be re-derived after the change and the surviving prong recorded. Three candidate causes are on the table and the RFC does not presume which holds:
   - `presentation_forms` — a verdict defect, fixed by this deliverable;
   - `single_letter_fragments` (`garble.py:391-395`) — genuine Arabic shaping loss, out of scope, to be documented rather than suppressed;
   - **wrong-language OCR** — `issue/data2_fixes_validation_report.md` (2026-06-30) attributes this document's failure to a corrupt CMap plus `ara` tessdata being absent or unselected, producing *"Latin mojibake"* from English OCR over Arabic script. That predates the `d5f0c19` tessdata-probe fix, so it may already be resolved — but it SHALL be ruled in or out from the persisted `fired_prongs` (D2) rather than assumed.

### Requirement 6: Flat Verdicts From Flat Signals (C3)

**User Story:** As a flat-routed document, I want my verdict computed from my own blocks, so that the reason recorded against me describes what was actually stored.

#### Acceptance Criteria

1. `compute_verdict` SHALL NOT silently ignore its `structure` argument. When called from the flat path (`indexer.py:1122`), the gate signals used SHALL be derived from `flat_structure`, not from the tree `TreeGateResult` carried in `state.gate_result`.
2. The mechanism SHALL be explicit rather than implicit: `evaluate_gates`' current behaviour of preferring `validate_result.signals` and falling back to `structure` only when `sig is None` (`verdict.py:151,164-167`) SHALL be replaced by a caller-declared choice, so no future call site can pass a dead argument unknowingly.
3. The flat leaf ratio already computed at `indexer.py:1132` SHALL be the value that reaches both the verdict and the sidecar. The sidecar's `max_leaf_ratio` and its `verdict_reason` SHALL be derived from the same structure.
4. Image blocks SHALL be visible to the flat garble gate. `block_text(block, CHAR_COUNT)` returns `""` for `role == "image"` (`helpers/flat.py:247-256`) — it ignores `block["text"]` entirely — so the block is skipped at `garble.py:805-806` before `detect_garble` is ever called. Chart and figure OCR text is therefore excluded from `_garble_check_flat_blocks` (`indexer.py:1032-1036`) and from `flat_char_count` (`indexer.py:1140`). The garble check SHALL see that text.

   > **Measured 2026-09-15 — this is a garble *escape*, not merely undercounting.** A smoke-test re-ingest of Doc 13 produced `MARGINAL / image_enrichment_partial(ratio=0.33)` while its stored blocks still read `"2025 et: - At galls all gus (98 Allen! an jgi"` — the same Latin-transliteration gibberish Run 8 failed it for. Garbled content is being persisted as MARGINAL, which is a **Hard Rule #5 surface**. A verdict moving FAIL → MARGINAL on this document is a masking, not a repair, and any corpus diff must read it that way.

5. The flat path SHALL apply the same image-specific garble threshold the tree path applies. `indexer.py:960-972` builds `_image_garble_cfg = GarbleConfig(garble_nonsense_ratio=IMAGE_OCR_NONSENSE_RATIO)` when `ext in _IMAGE_EXTS` and passes it to `validate_tree`; the flat gate at `indexer.py:1032-1036` passes the plain module-level `_garble_config` instead.

   > **This makes `d1f67c3` route-dependent.** That commit introduced `IMAGE_OCR_NONSENSE_RATIO = 0.45` *specifically* so the garble gate would catch Doc 13 (RUN-8 addendum: "Fix #3 — IMAGE_OCR_NONSENSE_RATIO=0.45 now fires garble gate correctly"). On the flat route the fix is inert — twice over: the threshold is not passed, and image blocks are skipped before any threshold could apply. A fix that only holds on one of two routes is not a fix.
6. The flat garble gate SHALL run after `_apply_picture_enrichment` (`indexer.py:1092`), or run again over enrichment-mutated blocks, so that blocks created or modified by enrichment are checked. *(Enrichment writes `ocr_text` into image blocks at `client/images.py:261-315`.)*
7. Downstream predicates SHALL receive flat signals on the flat path: `_try_cat_b` (`verdict.py:313-336`) and `_try_image_enrichment`'s `node_count` and character-floor checks (`verdict.py:242-249`).
8. Unit tests SHALL verify that a flat document whose tree had `max_leaf_ratio=0.86` and whose flat blocks have a materially different ratio receives the flat value.
9. This deliverable SHALL be treated as verdict-distribution-affecting and gated accordingly (Requirement 9).

### Requirement 7: Arbitrate on the Extraction, Not the Tree (C4)

**User Story:** As a recovery pass that produced 30,000 characters of clean Arabic, I want to be judged on the text I produced, not on whether a language model could build a tree from it.

#### Acceptance Criteria

1. `_execute_ocr_retry` (`recovery.py:236`) SHALL evaluate the recovered markdown for garble and content volume **before** `_reconvert_and_revalidate` (`indexer.py:423-450`) rebuilds a tree from it.
2. When the recovered markdown is materially better than the pre-retry markdown — not garbled, and substantially higher in content — that result SHALL be retained even if the tree rebuilt from it still fails validation. The stored outcome in that case SHALL be a truthful FAIL or MARGINAL over the better extraction, never a silent revert to the worse one. *(Hard Rule #5: the tree is still not silently persisted as good; what changes is which extraction the verdict is computed over.)*
3. `_keep_best_wins` (`recovery.py:92-208`) SHALL accept an arbitration input that is not derived solely from post-LLM tree text.
4. The two independent OCR arbitrators SHALL be reconciled: `_keep_best_wins` (char count + `_repeating_token_density`) and `client/images.py:296-309` (`_ocr_information_density`, alnum+digit ratio, 1.5× preservation, concatenating otherwise). They SHALL share one scoring policy, and that policy SHALL be script-aware — both current scorers are script-blind, which is the failure mode the RFC-045 escape at `recovery.py:184-198` exists to patch around.
5. The arbitration signature SHALL NOT hardcode a two-candidate comparison. *(This is a structural requirement, not an engine requirement: a third candidate arises from Requirement 4's corrective re-OCR, independent of RFC-047.)*
6. `tests/test_zone3_ocr_recovery.py` pins the current `_keep_best_wins` keyword signature; it SHALL be updated in the same change.
7. A unit test SHALL reproduce Doc 17: a recovery pass yielding clean Arabic markdown, a tree rebuild that still fails, and an outcome that retains the better extraction.
8. Requirement 7 SHALL NOT change `PRE_GARBLE_FORCE_OCR_ENABLED`'s default. Doc 17's recovery already fires and already produces the good markdown; the defect is what happens to it afterwards.

### Requirement 8: Density Numerator Correctness and Flag Parse Consistency

**User Story:** As a scanned document whose content lives partly in node summaries and figure OCR, I want the density gate to count the content I actually have.

#### Acceptance Criteria

1. `_flatten_tree_text` (`tree_validation.py:137-158`), as consumed by `_gate_suspect_density` (`gates.py:239-255`), SHALL count content that is genuinely stored and retrievable — including node `summary` and image-block `ocr_text` — or the density gate SHALL use a numerator that does.
2. `RFC029_MIN_SCANNED_DENSITY_FLOOR` SHALL NOT change value. This deliverable corrects what is measured, not where the line sits.
3. **Measure-first (OQ5, resolved 2026-09-15).** The corrected numerator SHALL first run in **report-only mode**: it computes both the old and new figure for every document and emits a per-document table showing which verdicts would change and by how much. It SHALL NOT alter any verdict in that mode.
4. Activation SHALL be a separate, explicit decision taken on that table. Until activated, the corrected numerator is observational only and the density gate behaves exactly as it does today.
5. `config.py:505-508` SHALL parse `PRE_GARBLE_FORCE_OCR_ENABLED` with the same predicate as its siblings at `:492-504` — `.strip().lower() in ("1","true","yes")` — so that `=1`, `=yes`, and values with surrounding whitespace are no longer silent no-ops. The default SHALL remain `false`.
6. A unit test SHALL verify `PRE_GARBLE_FORCE_OCR_ENABLED=1` and `=yes` are truthy and `=false`/unset are falsy.
7. Given criterion 5, the 2026-09-09 Doc-17 experiment recorded at `RUN-8:207` SHALL be re-run and its spelling confirmed before its result is relied upon anywhere.

### Requirement 9: Attribution-Gated Corpus Validation

**User Story:** As a maintainer who has watched five consecutive RFCs fix and re-break the verdict boundary, I want every verdict movement in this RFC attributed to a named deliverable before it is accepted.

#### Acceptance Criteria

1. Requirements 2 and 3 SHALL land and a corpus run SHALL be taken **before** any of Requirements 4–8 are merged, establishing an attributed baseline.
2. After Requirements 4–8, a full corpus run SHALL produce a per-document table naming, for every verdict change, the deliverable responsible.
3. Verdict movements SHALL be reported in both directions. A document moving FAIL → PASS and a document moving PASS → FAIL are both outcomes of interest; neither SHALL be omitted.
4. A document whose verdict improves without an identifiable responsible deliverable SHALL block acceptance pending explanation — an unexplained improvement is as much a signal of a measurement defect as an unexplained regression.
5. `CURRENT_PIPELINE_VERSION` (`config.py:15`) SHALL be bumped from 4 to 5 in the same commit as the first merged change that can reclassify the corpus, per RFC-014 D3. Remote Docling re-baselining does not apply (see Environment). *(2026-09-15: Wave 1 contains no such change — all of it is additive attribution — so the bump moves to the first Wave 3 deliverable. See task 1.9.)*
6. The corpus gate SHALL be coordinated with RFC-041 task 3.5a, which owns the full-corpus verdict-diff baseline.
7. Every before/after comparison in criteria 2–4 SHALL resolve a document's prior verdict by `doc_name` **and latest `processed_at`**, never by assuming one row per document, and the baseline report SHALL state how many superseded rows each document had. *(Added 2026-09-15; mechanism corrected same day — see below.)*

   **Corrected.** This criterion was first written as "set `VERDICT_DOWNGRADE_ENABLED=true` so the verdict CAS cannot suppress a downgrade". That rationale is wrong. `doc_id` is a fresh `uuid.uuid4()` per ingestion (`client/indexer.py:1272`) and the upsert is `ON CONFLICT (doc_id)` (`registry/queries.py:53`), so a re-ingestion always INSERTs a new row and the verdict CAS never executes on this path. The CAS governs only the reconcile and retry paths, which re-upsert an existing `doc_id`. The flag is set for the baseline anyway — harmless, and correct for those paths — but it is not what protects criterion 3.

   The real hazard is identity, and it is worse. The registry has no per-filename key: each ingestion adds a row and a full set of `processed/{doc_id}.*` objects, and nothing supersedes the old ones. `سياسة حوكمة و إدارة البيانات - Copy.pdf` already holds **four** rows in the working bucket. A naive "previous verdict for this file" lookup can therefore return any of them, and a naive count of corpus verdicts double-counts.

8. **A coverage change is not a verdict movement.** A document that had no scored row in the attributed baseline and reaches a verdict later has *gained coverage*; criteria 3 and 4 do not apply to it, because there is no prior verdict for it to have moved from. Such a document SHALL be reported in its own section of the delta table together with the reason it was uncovered at baseline. As of the 1.C baseline exactly one document qualifies — `world-stats-pocketbook`, which did not complete, for the reason D11 addresses. Without this criterion R9.4 would block acceptance on the very outcome D11 exists to produce. *(Added 2026-09-17.)*

### Requirement 10: Zone 2 Closure — post-NFKC ScriptContext call sites (D10)

**User Story:** As the garble detector, I want every `ScriptContext` I am given to have been constructed before NFKC destroyed the evidence, so that my verdicts are based on signals that still exist.

Adopted with Zone 2 ownership (OQ2). RFC-040 D6 reordered NFKC-before-bidi only in `_pre_inference_normalize`, leaving seven post-NFKC `ScriptContext` construction sites broken — the "RFC-040 Zone 2 pattern" the ownership manifest exists to catch.

#### Acceptance Criteria

1. Each of the seven post-NFKC `ScriptContext` call sites SHALL be enumerated by file and line, and each SHALL be either corrected to construct its context pre-NFKC or documented as genuinely unaffected with the reason recorded.
2. No `ScriptContext` used for garble or bidi decisions SHALL be constructed from text that has already been NFKC-normalized, unless the signal it carries is provably NFKC-invariant.
3. An architecture guard SHALL enforce criterion 2 so the pattern cannot silently reappear — this is its third recurrence (RFC-040 D6, RFC-045 `2c39168`, and D5 of this RFC all addressed different instances of it).
4. `audit/zones/ZONE_OWNERSHIP.yaml` `zone_2.successor_rfc` SHALL be set to RFC-046, and `zone_2.resolved` SHALL be set true only when criteria 1–3 hold.
5. D10 SHALL land in Wave 3 alongside D5, which shares its subsystem.

### Requirement 11: Reachable Dynamic Child Timeout (D11)

**User Story:** As the corpus run that has to finish, I want the page-count-derived budget the pipeline computes for a large document to be the budget that document actually gets, and when a child dies I want to know why.

Found 2026-09-17 while diagnosing why `world-stats-pocketbook` (292pp) never reached a verdict in the 1.C baseline. Three defects in one subsystem.

#### Acceptance Criteria

1. `effective_timeout` SHALL exceed the dynamic budget whenever the dynamic budget exceeds the static floor. Today `CHILD_TIMEOUT = JOB_TIMEOUT - CHILD_GRACE_SECONDS`, and `JOB_TIMEOUT` is itself sized as *max dynamic child timeout + buffer + grace* (`worker/constants.py:5-43`). The floor in `max(CHILD_TIMEOUT, chunked_docling_timeout_s(n))` (`worker/subprocess_mgr.py:163`) is therefore derived from a ceiling built to contain the dynamic value, and the `max()` can never select the dynamic branch — for any page count up to `MAX_DOCLING_PAGES`. A 292-page document computes a 3300s budget and is handed 3600s of a floor that was sized to hold it.
2. A property test SHALL assert criterion 1 across the whole domain of `chunk_count` and SHALL **fail against HEAD** before any fix lands. `tests/test_worker.py:330` and `:526` currently both pass while asserting contradictory things about this boundary; neither is a regression test for it.
3. The outer bound applied by every caller of `_run_converter_subprocess` SHALL be consistent with the inner bound it wraps. There are exactly two callers — `preprocess_client._process_one` (no outer bound at all) and `worker.job.process_document_job` (arq `job_timeout=3630`, `worker/lifecycle.py:143`, applied at `arq/worker.py:570`) — and a bound fixed before the document is seen cannot track one derived after the page count is known. The arq bound SHALL be set per-function, from the same derivation the child uses.
4. `MAX_EFFECTIVE_TIMEOUT` SHALL be re-derived rather than retained at its present `54000`, which carries no recorded derivation. Once arq's bound stops binding, that 15-hour rail becomes reachable, and with `MAX_JOBS_DEFAULT = 1` a single hung document holds the queue for all of it.
5. An architecture guard SHALL enforce criterion 3, so a third caller of `_run_converter_subprocess` cannot be introduced without an outer bound.
6. The child's stderr SHALL be retained on the timeout path. `subprocess_mgr.py` kills the process group and re-raises **before** `proc.communicate()`, so `stderr_bytes` stays `b""` and a timeout is indistinguishable from a hang. This is why the originating question — was `world-stats-pocketbook` slow or non-terminating? — is still unanswered.
7. D11 SHALL produce **no verdict movement** on any document the 1.C baseline scored. It changes how long a conversion may run, never what is measured. A movement attributed to D11 is a measurement defect under R9.4, not a result.

### Requirement 12: Phase and Decision-Layer Logging (D12)

**User Story:** As the maintainer watching a corpus run, I want to see which document is in which phase and which branch the pipeline took to reach its result — reconstructable for a single document, from logs alone.

Requested by the owner on 2026-09-17. The Grafana/Loki shipping connection is **deferred to a later RFC**; the log *format* is designed here so that deferral is a configuration file rather than a rewrite.

**The finding that shapes this deliverable.** The entire per-document pipeline — `index()`, every gate, every route decision, every recovery rung — runs inside the `converters_cli` **child process**, on both real routes (the arq worker and `preprocess_client.py:149`, which the corpus work mandates). `worker/subprocess_mgr.py:203` reads that child with `proc.communicate()`, and `stderr_tail` is never referenced again when the return code is 0. **Child stderr is buffered whole in the parent and discarded on success**; on failure only the last 4000 bytes survive inside an exception message, and nothing at all is visible until the document finishes. So today, at every log level, every line the decision layer would emit is thrown away. Child-stderr passthrough is therefore the gating task of D12, not a detail of it — and **it subsumes D11's task 3.13**, which retains stderr only on the timeout path.

#### Acceptance Criteria

1. Log records SHALL be one JSON object per line on **stderr**, with a frozen `v: 1` schema, snake_case keys, no dots in key names, and `attrs` exactly one level deep. A record SHALL NEVER span two lines — tracebacks live in `exc.stack` as a single escaped string.
2. The child process's stderr SHALL reach the parent's stderr **as it is produced**, streamed rather than buffered, while preserving the handshake read, the timeout budget, OOM detection (`CONVERTER_CHILD_OOM_TOTAL` fires on `SIGKILL` and must not be triggered by parent memory growth), and `ConverterChildError`'s `error_class` extraction. A bounded ring buffer SHALL retain `stderr_tail`.
3. Correlation SHALL be carried by a `contextvars`-backed `logging.Filter` installed on the root **handler**, so library loggers are covered and none of the 48 existing `getLogger` modules is modified. `doc_sha8` SHALL bridge the window before `doc_id` exists **within the child process only** — see the correction below. The context SHALL cross the process boundary by environment variable, following the `PAGEINDEX_JOB_START_CONFIG` precedent at `subprocess_mgr.py:125`.
   **Corrected 2026-09-18, during implementation.** As first written this criterion assumed `doc_sha8` is available wherever `doc_id` is not. It is not. `sha256` is computed at `client/indexer.py:1501`, which runs **inside the `converters_cli` child**, downstream of every parent-side bind site — so parent-side records carry neither `doc_sha8` nor `doc_id`. There is also no single universal key, because the two routes bind differently: the arq worker binds `run_id` + `job_id` (`worker/job.py:109`) and **never binds `doc_name`**; the batch route binds `run_id` + `doc_name` (`preprocess_client.py:152`) and never binds `job_id`. The spanning key for a whole document is therefore `run_id` plus whichever of `job_id` / `doc_name` that route bound, with `doc_sha8` and `doc_id` as late-arriving enrichments on a document's later records only. Task 12.10's resolution is consequently two-pass: an identifier resolves to a key, and the key returns the records — including those predating the identifier.

4. A single `Phase` enum SHALL name the phases a document passes through, derived from the code rather than invented, with a `phase_seq` that disambiguates phases re-entered across recovery passes.
5. Every decision point in the enumerated `DECISION_POINTS` registry SHALL emit `event`, `choice`, `reason` and bounded `attrs` sufficient to reconstruct the branch. Where a decision is *overridden*, the record SHALL carry both the computed outcome and the forced one — `force_route` currently overwrites `decide_route`'s answer with no trace of either.
6. Decision records SHALL be emitted at **INFO**, not DEBUG. A normal corpus run must answer "what flow did this document take"; a level that hides it fails the requirement.
7. **No document text SHALL be logged at any level.** Default-deny, per Hard Rule 3 and the `tracing.py::_mask` doctrine: no `md_content`, node text, summaries, table cells, OCR output, LLM prompts or completions, and **no node titles** — a German insurance heading can name an insured party. Lengths, ratios, `node_path`, and 8-hex digests instead. Absolute filesystem paths SHALL be reduced to a basename. Enforced by an AST guard over every emitter call site's `attrs` keys.
8. `decision()` and `phase()` SHALL NEVER raise — the posture `tracing.py:168` already takes ("tracing must never break the tool"). In particular they SHALL be emitted outside `finalize_gate_and_route`'s `_guard_bypass` window, and SHALL NOT write to `ExtractionState`, whose `__setattr__` single-writer guard (`types.py:241-251`) would reject it.
9. Every emitter SHALL be guarded by `logger.isEnabledFor(...)` before its `attrs` dict is built — constructing the dict is the cost, not the emit. Per-node records SHALL be DEBUG-only and capped.
10. Logging configuration SHALL read the environment **once at import** in its own module, and SHALL NOT be added to `PipelineConfig.from_env`. Both halves are load-bearing: `TestHotPathConfigAccessGuard` (`test_architecture_guards.py:772-838`) forbids inline env reads in five of the six files that receive decision records, and `TestNoConfigDoubleSourcing` (`:1430`) is closed-world over `src/`, so registering these vars in `from_env` would make the logging module's own read a violation.
11. The configured handler's stream SHALL be `sys.stderr`. `converters_cli` reserves stdout for exactly two JSON lines (`:31`, `:56-62`); a handler defaulting to stdout would fail every job with `invalid JSON on stdout`. Enforced by a guard test.
12. D12 SHALL be **behaviour-neutral**: it reclassifies no document and moves no verdict. Like D11, a verdict movement attributed to D12 is a measurement defect under R9.4.
13. A read-only `scripts/logtrace.py` SHALL reconstruct one document's ordered record sequence from a captured log file, proving criterion 5 without any Loki dependency.

**Out of scope, explicitly:** Loki, promtail, Alloy, Grafana dashboards or alerts; log retention policy; migration of the 48 modules' message strings to event names; structlog; an OpenTelemetry logs bridge; any metric change; logging inside the five OCR sidecar services. **Named for the deferred shipping RFC:** the moment logs leave the host, HR2's erasure cascade acquires a new derived store. That must be addressed there.

## Decision Summary

### D1: Reproducible Evaluation Evidence (Requirement 1)

Repair the eval harness's language derivation, comparison keys, and connection-failure handling; re-run at full page count; commit complete artifacts; regenerate the report from them. Until then, mark the existing report unverified and cite it nowhere.

### D2: End-to-End OCR Attribution (Requirement 2)

Introduce `OcrEngine`; add `engine` to `OcrDecision`; label all five OCR invocation sites including the previously-unenumerated `_landscape_rasterize_rotate_reextract`; stop hardcoding `used_converter`; persist `fired_prongs` on both the tree and flat persistence paths; label `OCR_ESCALATION_TOTAL`. Keep `decide_ocr_strategy` at one call site.

### D3: Corrected Run-8 Baseline (Requirement 3)

Recount the tally, confirm the Doc 18 omission, correct the branch header, record as a dated addendum.

### D4: Content-Derived OCR Language Selection (Requirement 4)

Replace filename-only language derivation on the image path with a bounded detect-correct-retry, mirroring the union already used at `recovery.py:291-297`; make garble recovery reachable for image inputs.

### D5: Presentation-Forms Detector Alignment (Requirement 5)

Bring the two threaded `any(...)` detectors onto the shared `>0.50` ratio, separating detection sensitivity from NFKC triggering. Completes what `2c39168` began.

### D6: Flat Verdicts From Flat Signals (Requirement 6)

Make the flat path compute gate signals from flat blocks; make the argument-vs-`gate_result` choice explicit at the call site; make image-block OCR text visible to the flat garble gate and re-check enrichment-mutated blocks.

### D7: Arbitrate on the Extraction (Requirement 7)

Quality-check recovered markdown before tree rebuild; retain a materially better extraction even when its tree fails; reconcile the two arbitrators onto one script-aware policy; drop the two-candidate assumption.

### D8: Density Numerator and Flag Parse (Requirement 8)

Correct the density numerator to count stored-and-retrievable content; leave the floor value untouched; align the `PRE_GARBLE_FORCE_OCR_ENABLED` parse predicate with its siblings.

### D9: Attribution-Gated Corpus Validation (Requirement 9)

Baseline first, then remediate, then attribute every movement in both directions. Bump the pipeline version; coordinate with RFC-041 3.5a. **Remote Docling re-baselining does not apply** — Docling runs in-process (see Environment), so the `client/remote.py:62` version handshake is out of the loop. **Adopt RFC-042 task 4.2 (config consistency property test) into this RFC** — D2 and D8 both add or change config surface, and 4.2 is the guard that prevents repeating the `PRE_GARBLE_FORCE_OCR_ENABLED` double-sourcing and parse-asymmetry defects (see OQ1).

### D10: Zone 2 Closure — post-NFKC ScriptContext sites (Requirement 10)

Enumerate and correct the seven post-NFKC `ScriptContext` construction sites RFC-040 D6 left behind, and add an architecture guard so the pattern cannot recur. Adopted together with Zone 2 successor ownership (OQ2). Lands in Wave 3 alongside D5, which shares its subsystem.

This is the **third** distinct instance of one pattern — RFC-040 D6, RFC-045 `2c39168`, and D5 each fixed a different site where a signal was read after NFKC had destroyed the evidence. The guard, not the seven fixes, is the durable deliverable.

### D11: Reachable Dynamic Child Timeout (Requirement 11)

Break the floor/ceiling derivation so `max(CHILD_TIMEOUT, chunked_docling_timeout_s(n))` can select the dynamic branch; set arq's per-function timeout from that same derivation via `func(process_document_job, timeout=...)` so the batch CLI and the worker stop disagreeing; re-derive `MAX_EFFECTIVE_TIMEOUT`; retain child stderr on the timeout path. Infrastructure, not behaviour — no signal, threshold or verdict input changes. Lands in Wave 3 as tasks 3.10-3.13, failing property test first.

**A second deliberate scope expansion**, after D10. A follow-up RFC was the alternative and was rejected: the 1.C baseline was taken through the batch CLI, which applies no outer bound, while production runs under arq's. The two do not share timeout semantics, so until D11 lands **every Wave 3-6 comparison that touches timeouts is invalid against that baseline**. See R9.8 and the 1.C blind spots.

### D12: Phase and Decision-Layer Logging (Requirement 12)

Stdlib `logging` plus `extra=`, a JSON `Formatter`, and a `contextvars`-backed `Filter`. **No structlog, no new dependency.** The argument is not cost-aversion: structlog's bound context does not reach a `logging.getLogger` module, so correlation across the 48 existing modules needs a stdlib filter either way — which leaves structlog paying only for rendering that sixty lines of `Formatter` already does. It would start to pay if the five OCR sidecar services needed the same pipeline, or if per-logger processor chains became a requirement; neither is in scope.

Lands as **Wave 2.5**, in two tranches, by owner decision on 2026-09-17:

- **Core (tasks 12.1-12.4, 12.10, gate 12.C-core)** — formatter, correlation, child-stderr passthrough, phase model, and the reconstruction tool. This alone makes a run observable end to end and discharges D11's 3.13. **Wave 3 waits for the core and nothing more.**
- **Instrumentation (tasks 12.5-12.9)** — the `DECISION_POINTS` registry across ten modules, redaction guard, config module, guard tests, and the `basicConfig` unification. Lands as a second tranche, parallelisable with Wave 3.

Behaviour-neutral, so it perturbs neither the 1.C baseline nor task 1.9's deferred version bump. Estimated ~35h total, of which the core is ~17h — larger than D5 and D8 combined, and stated rather than sold.

## Implementation Plan


### Phase naming across the three artifacts

Three vocabularies are in use and they describe the same work. This table is the translation; it appears identically in the plan, the RFC and the tasks file.

| Plan (§6) | RFC (Implementation Plan) | Tasks | Deliverables |
|---|---|---|---|
| **P0** · Baseline truth | Phase 0 — Baseline | **Wave 1** | D2, D3, RFC-042 4.2 |
| **P0** (evidence arm) | Phase 1 — Evidence | **Wave 2** | D1 |
| *(beyond plan §6)* | Phase 1.5 — Observability | **Wave 2.5** | D12 |
| **P0.5** · Cluster fixes | Phase 2 | **Wave 3** | D5, D8, D10, D11 |
| **P0.5** | Phase 3 | **Wave 4** | D6 |
| **P0.5** | Phase 4 | **Wave 5** | D7 |
| **P0.5** | Phase 4 | **Wave 6** | D4 |
| *(beyond plan §6)* | Phase 5 | **Wave 7** | D9 |
| *(beyond plan §6)* | — | **Wave 8** | RFC-047 go/no-go |

In short: **P0 = Waves 1–2, P0.5 = Waves 3–6.** Waves 7–8 are the corpus validation and the RFC-047 decision, which the plan's §6 framing did not cover. RFC-046 scopes P0 and P0.5 only; the plan's P1–P4 belong to RFC-047 if it is written.

### Sequencing

1. **Phase 0 — Baseline** (D2, D3, and RFC-042 4.2 per D9). Attribution must exist before anything can move, or movements cannot be explained. Corpus run at the end of this phase is the graded baseline.
2. **Phase 1 — Evidence** (D1). Independent of the code work; parallelizable with Phase 0. Gates any RFC-047 claim, not this RFC's own deliverables.
3. **Phase 2 — Independent cluster fixes** (D5, D8, D10, D11). D5 and D10 share the garble/normalization subsystem and land together; D8 is independent and runs report-only (OQ5). D11 is infrastructure and touches none of their subsystems, so it can land at any point in the wave — but its stderr-retention criterion (R11.6) is the only item that yields a diagnosis rather than a larger budget, and should land first if the wave is cut short.
4. **Phase 3 — Verdict plumbing** (D6). Touches `evaluate_gates`' signal-selection contract; must land alone so its corpus delta is attributable.
5. **Phase 4 — Recovery arbitration** (D4, D7). D7's arbitration reconciliation is a prerequisite for D4's corrective re-OCR, since that introduces a third candidate. Land D7 then D4.
6. **Phase 5 — Corpus validation** (D9). Full run, attributed per-document delta table, pipeline-version bump, remote re-baseline.

### Effort Estimate

| Phase | Deliverable | Effort | Risk |
|---|---|---|---|
| 0 | D2: Engine + prong attribution across 5 sites, 2 persistence paths, sidecar, metric | ~8h | Medium — touches many call sites; guard tests constrain the shape |
| 0 | D3: Baseline correction | ~1h | Low — documentation |
| 0 | RFC-042 4.2 adoption: config consistency property test | ~2h | Low |
| 1 | D1: Harness repair + full re-run + artifact commit | ~6h | Medium — re-run is long-running; engine services must actually start |
| 2 | D5: PF detector alignment + shared threshold | ~3h | Medium — garble sensitivity change; broad test fixture impact |
| 2 | D8: Density numerator + flag parse | ~3h | Medium — numerator change moves density verdicts |
| 3 | D6: Flat verdict signal selection + image-block visibility | ~8h | **High** — changes verdict inputs for every flat-routed document |
| 4 | D7: Markdown-level arbitration + arbitrator reconciliation | ~8h | **High** — rewrites the keep-best contract; pinned by tests |
| 4 | D4: Content-derived language selection + image recovery rung | ~6h | Medium — new recovery method must satisfy two AST guards |
| 2 | D10: Zone 2 closure — 7 post-NFKC ScriptContext sites + guard | ~8h | Medium — third recurrence of one pattern; guard is the durable part |
| 5 | D9: Corpus validation, attribution table, version bump | ~6h | Medium — long-running; coordination with RFC-041 3.5a |
| 2 | D11: Timeout floor/ceiling fix + arq per-function bound + stderr retention + guard | ~6h | Medium — widens a production timeout rail; property test pins the invariant |
| 1.5 | D12 core: JSON formatter, correlation, child-stderr passthrough, phase model, logtrace | ~17h | Medium — the passthrough touches the converter child boundary |
| 1.5 | D12 instrumentation: 19 decision points across 10 modules, redaction, guards | ~18h | **High** — five of the ten files are hot-path and guard-constrained |
| **Total** | | **~100h** | **(revised 2026-09-17 from ~65h: +D12, Requirement 12, at owner request. Same day from ~59h: +D11, Requirement 11. Previously revised 2026-09-15 from ~51h: +D10 via Zone 2 ownership, OQ2)** |

## Test Strategy

- **D2:** Architecture guard asserting every enumerated OCR site labels its engine, and that `decide_ocr_strategy` still has exactly one call site in `converters/pictures.py` (`test_architecture_guards.py:1089-1114` must pass unmodified). Unit tests asserting `fired_prongs` reaches the sidecar on both the tree and flat paths. Sidecar schema test per the `test_config.py:214` pattern.
- **D3:** No automated test; verified by recount against the scorecard rows.
- **D1:** Harness self-test asserting a non-zero exit on an unreachable endpoint. Artifact completeness check: every enabled engine has non-zero data for every processed document.
- **D5:** Boundary tests either side of the 0.50 ratio; a one-ligature negative case; a PF-dominated positive case; regression tests pinning Docs 19/21/23. A test asserting NFKC still runs when any presentation form is present — the separation in criterion 3 is the subtle part and needs its own test.
- **D10:** Per-site test for each of the seven post-NFKC `ScriptContext` constructions, or a recorded justification where a site is provably NFKC-invariant. Architecture guard asserting no `ScriptContext` used for a garble or bidi decision is built from NFKC-normalized text.
- **D6:** Unit test with divergent tree and flat leaf ratios asserting the flat value reaches both verdict and sidecar. Test that image-block `ocr_text` is counted by the flat garble gate and by `flat_char_count`. Test that enrichment-mutated blocks are garble-checked. A guard test that `compute_verdict`'s `structure` argument cannot be silently ignored.
- **D7:** Doc 17 reproduction — recovery yields clean markdown, tree rebuild fails, better extraction retained. Arbitration tests with three candidates. A script-awareness test: formal Arabic must not lose to Latin gibberish on any scorer. `tests/test_zone3_ocr_recovery.py` updated in the same change.
- **D4:** Latin-filename/Arabic-content image test. Bounded-retry test (at most one corrective pass). AST guard conformance for any new recovery method (`full_page_already_applied` guard, `_all_defects` predicate).
- **D8:** Density numerator unit tests over summaries and image OCR text. Env parse tests for `1`, `yes`, `true`, whitespace, `false`, unset.
- **D12:** Guard tests are the deliverable's spine, not its trim: every `DECISION_POINTS` entry actually emits; one line per record; the handler stream is `sys.stderr`; no `logging.basicConfig` outside the observability module; the six hot-path files still pass `TestHotPathConfigAccessGuard`; an AST scan of every emitter call site for banned `attrs` keys. Plus a two-document reconstruction through `scripts/logtrace.py` — one clean Latin, one Arabic that garbles — confirming the flow is recoverable and that no text or title string appears in the output.
- **D11:** A property test over the full `chunk_count` domain asserting `effective_timeout` exceeds the dynamic budget and does not exceed the caller's outer bound — written to fail against HEAD (R11.2). Reconcile `test_worker.py:330` against `:526`, which contradict each other today. Architecture guard enumerating `_run_converter_subprocess`'s callers and asserting each declares an outer bound. A unit test asserting stderr survives the timeout path.
- **Corpus:** Attributed per-document delta table, both directions, after Phase 4. Baseline taken after Phase 0. Coverage gains reported separately from verdict movements per R9.8.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| D6 and D8 shift the verdict distribution and "reveal" failures the prior inputs masked | **High** — cross-session history records this as a 6–7-cycle chronic pattern across five RFCs | Looks like regression; is actually unmasking | Attribution lands first (D9 criterion 1); every movement named; unexplained *improvements* block acceptance too (criterion 4); phases 2–4 land separately so deltas stay attributable |
| D5 raises the garble threshold and lets genuinely garbled Arabic through | Medium | A low-quality tree persists — Hard Rule #5 | The other three detectors already use 0.50; this aligns with them rather than inventing a threshold. Criterion 5 pins the PF-dominated positive case. Doc 22's surviving prong is recorded either way (criterion 7) |
| D7 retains a better extraction whose tree is bad, and this reads as persisting a low-quality tree | Medium | Hard Rule #5 violation if done carelessly | Criterion 2 is explicit: the verdict stays a truthful FAIL/MARGINAL. What changes is which extraction the verdict is computed over, not whether a bad tree is called good |
| D2's attribution work touches five OCR sites and destabilises them | Medium | Broad | `_tesseract_ocr_image` is a chokepoint with a never-raise contract pinned at `test_converters.py:916-1005` and monkeypatched by name at `:1623`; preserve both. Land D2 alone, with a corpus run, before any behavioural change |
| The fifth OCR site is missed again | Medium — it has been missed by every prior enumeration | Sidecar confidently wrong on Doc 17 | `_landscape_rasterize_rotate_reextract` is named explicitly in Requirement 2 criterion 3 and in Traceability |
| D4's new recovery method fails the AST guards late | Medium | Rework | Criterion 5 states both guard contracts up front |
| D1's re-run cannot start the engine services, reproducing the RFC-036 D7 false negative | Medium | Evidence remains absent | Criterion 3 makes a connection failure a hard error rather than a zero result |
| Zone specs and cross-session memory cited here are stale | Medium — precedent in RFC-044 §Context, and memory was found stale on the promotion cascade during planning | RFC argues against a fixed defect | Every claim in Context was re-verified against HEAD at `704d73a`. Re-verify again before implementation |
| Scope creep toward the engine question | Medium | This RFC becomes the thing it was split to avoid | Non-Goal 1 is explicit; RFC-047 is named |

## Consequences

- Every persisted verdict will name the engine and the prong behind it, making the next corpus diff attributable for the first time.
- The evaluation artifacts become reproducible, or they are not cited — closing the Hard Rule #1 exposure.
- Documents failing on `garbling` because of a single presentation-form codepoint will stop doing so. Doc 22 will either pass or fail on a different, recorded prong; if `single_letter_fragments` fires, we will have identified a genuine extraction defect that was previously hidden behind a false one.
- Flat-routed documents' verdict reasons will describe their flat blocks. Expect verdict movement across the whole flat population, not only Doc 14 — this is the largest blast radius in the RFC.
- Chart and figure OCR text becomes visible to the flat garble gate and to `flat_char_count`, which will move density and garble verdicts for image-bearing documents.
- A recovery pass that produces materially better text will no longer have that text discarded because a language model could not build a tree from it.
- `PRE_GARBLE_FORCE_OCR_ENABLED=1` will start working as operators reasonably expect. Any environment currently setting it that way has been running with it off; enabling it changes behaviour, including disabling the garble and low-content recovery rungs via `recovery.py:439,475`.
- The density gate will count content it previously ignored, moving some `suspect_density` verdicts without the floor having moved.
- **The engine question becomes decidable.** After the Phase 5 corpus run we will know which failures survive correct language selection, correct arbitration, correct flat verdicts, and a correct PF detector. RFC-047 is written against that residue, or not written at all.
- Zone 2 (`normalize-before-detect null-detector lattice`, owned by RFC-040, successor RFC-041 D10c) overlaps D5. Ownership must be coordinated rather than silently claimed — see OQ2.

### Out-of-scope defect found while preparing the 1.C baseline (2026-09-15)

**Re-ingestion orphans a full copy of every derived artifact, and HR2 erasure cannot reach it.** Not RFC-046's to fix; recorded because it is a Hard Rule surface and it distorts any corpus measurement taken from the registry.

`doc_id` is a fresh `uuid.uuid4()` per ingestion (`client/indexer.py:1272`). The hash cache normally prevents a second ingestion of an unchanged file by returning the existing `doc_id` (`indexer.py:1472-1481`), so duplicates appear only when that cache misses for a file already in the store — a cleared or evicted Redis `pageindex:hashes`, or a changed filename. When it does miss, the run writes a new row plus a complete new set of `processed/{doc_id}.json`, `.flat.json`, `.meta.json` and `figures/{doc_id}/*`, and **nothing supersedes or removes the previous set**.

`delete_doc` is keyed on a single `doc_id` (`storage/documents.py:180`), so erasing "the document" purges only the newest copy. Every earlier copy retains the full document text and remains addressable. The working bucket currently holds **four** copies of `سياسة حوكمة و إدارة البيانات - Copy.pdf`; the 1.C run adds one more copy for each of the 25 corpus documents, because the hash cache was deliberately cleared for it.

Two consequences:
1. **HR2 (right-to-erasure must cascade across every derived store) is not satisfied for any re-ingested document.** A DSR honoured through `delete_doc` leaves the orphans behind.
2. Any corpus tally read from `doc_registry` or from `processed/*.meta.json` double-counts re-ingested documents. This is why [R9.7](#requirement-9-attribution-gated-corpus-validation) requires resolving by `doc_name` + latest `processed_at`.

Suggested owner: a follow-up RFC that either derives `doc_id` deterministically from `sha256`, or supersedes prior `doc_id`s for the same `doc_name` at persist time, or extends the erasure manifest to sweep by `doc_name`.

### Observations recorded while landing Wave 2 (2026-09-17)

Found while repairing the eval harness (D1, tasks 2.1-2.4). None is a D1 defect; each is recorded here rather than fixed, so it is not rediscovered as news.

1. **`detect_ocr_langs` returns `['ara']` alone — without `eng` — for five corpus documents.** By design: `ocr_langs.py` appends `eng` to an Arabic-dominant sample only when Latin is *materially* present (`_MIXED_SCRIPT_MIN_RATIO`). If those five documents carry embedded Latin or numeric content below that ratio, **production is already dropping it today**, at every escalation site, not only in the harness. This belongs to **D4's wave (Wave 6)** and should be settled with a measurement over the five documents, not an argument about the threshold. Note the interaction with D4's own criterion: content-derived selection changes the sample this ratio is computed over.

2. **`detect_ocr_langs("")` returns `['deu','eng']` as an empty-input fallback, and production unions that fallback into real detections.** `pictures.py:1089` and `recovery.py:293-294` union `detect_ocr_langs(filename)` with `detect_ocr_langs(md or "")`. When the markdown export is genuinely empty — a scanned Arabic PDF, the exact case the docstring at `pictures.py:1066` describes — the union injects `deu` and `eng` into what would otherwise be an Arabic-only selection. The harness deliberately does **not** reproduce this (see task 2.2), because unioning a fallback into a detection is not a language decision. Whether production should keep doing it is a D4 question.

3. **`scripts/ocr_spike_eval.py::_run_pipeline` is a single ~270-line function** (`:912-1183`) carrying health checks, four processing phases, result assembly and report writing. It inherited that shape from the original `main()` and was deliberately left alone in Wave 2 to keep D1's diff attributable. It violates the project's 50-line guidance and is the reason the file had almost no seams to test against — the 30 tests added in Wave 2 mostly reach the runners directly rather than the pipeline. A D1-adjacent cleanup task, to be taken before task 2.5 grows it further with the artifact completeness gate.

4. **`winner_by_speed` is not a like-for-like comparison.** It sums `elapsed_s` across local in-process Tesseract and remote GPU-backed services on entirely different hardware paths. Any regenerated report that surfaces it must say so explicitly, or it reads as a benchmark it is not. Adjacent to Hard Rule 1's concern — an unqualified speed ranking invites the same category of claim the rule forbids on accuracy.

## Open Questions

All five original open questions were put to the owner on 2026-09-15 and answered. They are retained with their resolutions rather than deleted, so the reasoning behind each decision stays on the record.

1. **Should RFC-042 task 4.2 be adopted here, or should RFC-046 sequence behind RFC-042?** — **RESOLVED (2026-09-15): adopt it here.** D2 and D8 both add config surface, and 4.2 is the guard against repeating the double-sourcing and parse-asymmetry defect class this RFC is fixing. Blocking on RFC-042's other 13 tasks — including the `PROMOTION_ORDER` work this RFC does not need — was rejected. Task 1.7 stays in Wave 1.
2. **Who owns Zone 2 after D5?** — **RESOLVED (2026-09-15): RFC-046 takes full successor ownership.** `successor_rfc` transfers from RFC-041 to RFC-046 in `audit/zones/ZONE_OWNERSHIP.yaml`. This deliberately grows scope: RFC-046 becomes accountable for the seven post-NFKC `ScriptContext` call sites RFC-040 D6 left broken, which are now **Requirement 10 / D10**, landing in Wave 3 alongside D5.
3. **Does Requirement 7 criterion 2 need a Hard Rule #5 ruling?** — **RESOLVED (2026-09-15): yes, keep the better extraction.** Storing ~30,000 correct characters under an honest FAIL is consistent with Hard Rule #5, because the tree is still called bad; only the text underneath improves. Task 5.2 proceeds as specified. No additional sidecar divergence marker was required.
4. **How far should D6's contract change go?** — **RESOLVED (2026-09-15): the thorough path.** `evaluate_gates` gains a caller-declared signal source so a dead argument becomes impossible to pass, and a guard test enforces it. This touches every caller, accepted deliberately to prevent recurrence rather than to minimise blast radius.
5. **Is the density numerator (D8) in scope, or is it threshold work by another name?** — **RESOLVED (2026-09-15): in scope, but measure-first.** The corrected counter is built and run in **report-only mode**; it produces a per-document table showing exactly which documents would move and by how much, and activation is a separate decision taken on those real numbers. Task 3.4 is restructured accordingly. This removes the RFC author's estimate from the decision entirely and is a better shape than the original proposal.

6. **Where do the 25 corpus documents come from?** — **RESOLVED (2026-09-17): the owner supplied them and they are ingested.** The 1.C attributed baseline scored **24 of 25**; `world-stats-pocketbook` did not complete, for the reason D11 addresses. D4, D6 and D8 are no longer unit-coverage-only. See [[rfc046-wave1-attributed-baseline]].

### Still open

None. All six questions are resolved; the entries above are retained with their resolutions rather than deleted.

## Environment

**Resolved 2026-09-15.** All corpus runs execute with Docling **in-process** on the working host, against remote MinIO, Redis and Postgres. There is no remote Docling service in the loop.

| Component | State |
|---|---|
| Tesseract | 5.3.4 with `ara`, `deu`, `eng`, `osd` |
| Docling | in-process (`DOCLING_SERVICE_URL` empty → `use_remote=False`); models cached locally |
| MinIO | remote, bucket `pageindex` — reachable, read/write/presign verified |
| Redis | remote — reachable |
| Postgres | remote — reachable (a stale pod IP in a five-week-old `env/remote.env` was the earlier timeout; `make env-remote` fixed it) |
| Processing path | `preprocess_client.py` — a fresh `converters_cli` child, **no arq queue** |

Three environment facts that bear on the RFC:

1. **Fidelity is established.** A trial ingest of Doc 19 (سياسة حوكمة) reproduced Run 8 to within 2 characters: PASS/`structural_pass`, `max_leaf_ratio` **0.1873 exactly**, 18,289 chars vs 18,287. The local in-process route faithfully reproduces Run-8's pipeline for local-route documents.
2. **The arq queue must not be used.** Containerised workers from a separate `/app` deployment are live on the *same* Redis db and the *same* bucket, configured against the **remote** Docling service. Enqueuing work risks those workers processing it with different code, silently invalidating results. `preprocess_client.py` bypasses the queue entirely and is the required path.

   **Consequence for every baseline taken this way (2026-09-17).** `preprocess_client._process_one` applies **no outer timeout bound**; `worker.job.process_document_job` runs under arq `job_timeout=3630`. The 1.C baseline and production therefore do not share timeout semantics. No document that completed was affected — all 24 finished well inside both bounds — but the one that did not complete, `world-stats-pocketbook`, cannot be compared across the two paths, and neither can any Wave 3-6 result that touches timeouts, until D11 lands and the figures are re-taken through the worker path.
3. **The Run-8 baseline is unrecoverable.** Run 8 claims verification against "20 meta objects, all `processed_at` 2026-09-09"; the bucket holds 17, newest 2026-08-07, at an unchanged endpoint. **D3's tally correction therefore becomes a documentation deliverable only** — it cannot be re-derived from stored artifacts — and every gate anchors to a fresh attributed baseline taken under Requirement 9, not to Run 8.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design | [[design-rfc046-ocr-attribution-failure-cluster-remediation]] |
| Tasks | [[tasks-rfc046-ocr-attribution-failure-cluster-remediation]] |
| Pre-RFC plan | [[plan-rfc046-surya-quality-fallback]] (§2 clusters, §3 blockers, §6 P0/P0.5) |
| Successor | RFC-047 (OCR engine tier) — to be written against the Phase 5 residue |
| Supersedes | N/A |
| Zone Specs | [[garble-detection-nfkc-signal-destruction]] (**Zone 2 — RFC-046 is successor owner as of 2026-09-15; closed by D5 + D10**), [[ocr-pipeline-decision-recovery-cascade]] (Zone 1), [[verdict-promotion-hard-rule-5-bypass]] (Zone 4 anti-pattern) |
| Prior Art | [[RFC-045]] (`2c39168` PF fix, completed by D5), [[RFC-044]] (authority inversion, Phase B explicitly not attempted), [[RFC-043]], [[RFC-042]] (task 4.2 adopted per D9), [[RFC-041]] (task 3.5a corpus baseline), [[RFC-021]] (QF1 deferral doctrine, unchanged by D8) |
| Evidence: default no primary-pass OCR | `converters/docling_conv.py:77-80`; dark inputs `config.py:505`, `config.py:42,486` |
| Evidence: no engine identity | `grep -rn "ocr_engine\|OCR_ENGINE" src/` → empty; `recovery.py:341` hardcoded |
| Evidence: eval artifacts Tesseract-only | `agents/spikes/ocr_eval_rfc046/{eval_report,eval_full_detail,tess_results}.json` — 84,733 chars tesseract, 0 for surya/paddleocr_vl/paddleocr, 0/25 docs each |
| Evidence: C1 density | `gates.py:239-255`, `config.py:565`, `gates.py:440` (recovery_waived), numerator `tree_validation.py:137-158` |
| Evidence: C2 language selection | `indexer.py:893` vs `recovery.py:291-297`; `.pdf` gates `recovery.py:437`, `recovery.py:662` |
| Evidence: C3 flat verdict | `indexer.py:1122` + `verdict.py:151,164-167`; unused flat ratio `indexer.py:1132`; image blocks `helpers/flat.py:247-256` |
| Evidence: C4 arbitration artifact | `recovery.py:367-390`, `_keep_best_wins` `recovery.py:92-208`; second arbitrator `client/images.py:296-309`, scorer `:252-258` |
| Evidence: C5 PF asymmetry | `any(...)` at `indexer.py:190`, `normalize.py:159` vs `>0.50` at `garble.py:47`, `tree_validation.py:310`; prong `garble.py:388-389` |
| Evidence: C6 garble masks flat | `tree_validation.py:437-444`; `decide_route` `types.py:358-359` |
| Evidence: fifth OCR site | `converters/pipeline.py:376` `_landscape_rasterize_rotate_reextract`; implicated at `RUN-8:207` |
| Evidence: flag parse asymmetry | `config.py:505-508` vs siblings `config.py:492-504` |
| Evidence: `fired_prongs` dropped | produced `garble.py:533-535`; persisted set `indexer.py:1322-1323` |
| Constraints: architecture guards | `tests/test_architecture_guards.py:996,1035,1063` (OCR-retry AST guard), `:1089-1114` (single call site), `:1116-1131` (no unreachable flag), `:772-838` (hot-path env reads), `:1134` (eligibility symmetry) |
| Constraints: pinned contracts | `tests/test_zone3_ocr_recovery.py` (keep-best signature), `tests/test_converters.py:916-1005,1623` (`_tesseract_ocr_image`), `tests/test_facade_surface_guard.py:138` (frozen `__all__`) |
| Evidence: D11 floor/ceiling inversion | `worker/constants.py:5-43` (the self-describing derivation), `worker/subprocess_mgr.py:163` (`max(CHILD_TIMEOUT, ...)`), `chunked_docling_timeout_s` = `300 + n*1500`, `MAX_DOCLING_PAGES=150` |
| Evidence: D11 caller divergence | `_run_converter_subprocess` callers — `preprocess_client._process_one` (unbounded), `worker/job.py::process_document_job` under `worker/lifecycle.py:143` `job_timeout=3630`, applied `arq/worker.py:570` |
| Evidence: D11 discarded stderr | `worker/subprocess_mgr.py` timeout path kills the group and re-raises before `proc.communicate()`; `stderr_bytes` stays `b""` |
| Evidence: D11 contradictory tests | `tests/test_worker.py:330` vs `:526` — both pass, both cannot be right |
| Baseline | [[rfc046-wave1-attributed-baseline]] — 1.C gate, pass-with-caveats, 24/25 documents |
| Process: pipeline version | `config.py:12-15`; compared `client/remote.py:62` |
