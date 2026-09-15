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
7. Doc 22's verdict SHALL be re-derived after the change and the surviving prong recorded. If `single_letter_fragments` (`garble.py:391-395`) fires instead, that is a genuine extraction defect and SHALL be documented as out of scope for this RFC rather than suppressed.

### Requirement 6: Flat Verdicts From Flat Signals (C3)

**User Story:** As a flat-routed document, I want my verdict computed from my own blocks, so that the reason recorded against me describes what was actually stored.

#### Acceptance Criteria

1. `compute_verdict` SHALL NOT silently ignore its `structure` argument. When called from the flat path (`indexer.py:1122`), the gate signals used SHALL be derived from `flat_structure`, not from the tree `TreeGateResult` carried in `state.gate_result`.
2. The mechanism SHALL be explicit rather than implicit: `evaluate_gates`' current behaviour of preferring `validate_result.signals` and falling back to `structure` only when `sig is None` (`verdict.py:151,164-167`) SHALL be replaced by a caller-declared choice, so no future call site can pass a dead argument unknowingly.
3. The flat leaf ratio already computed at `indexer.py:1132` SHALL be the value that reaches both the verdict and the sidecar. The sidecar's `max_leaf_ratio` and its `verdict_reason` SHALL be derived from the same structure.
4. Image blocks SHALL be visible to the flat garble gate. `block_text(block, CHAR_COUNT)` returns `""` for `role == "image"` (`helpers/flat.py:247-256`), so chart and figure OCR text is excluded from `_garble_check_flat_blocks` (`indexer.py:1026-1036`) and from `flat_char_count` (`indexer.py:1140`). The garble check SHALL see that text.
5. The flat garble gate SHALL run after `_apply_picture_enrichment` (`indexer.py:1092`), or run again over enrichment-mutated blocks, so that blocks created or modified by enrichment are checked. *(Enrichment writes `ocr_text` into image blocks at `client/images.py:261-315`.)*
6. Downstream predicates SHALL receive flat signals on the flat path: `_try_cat_b` (`verdict.py:313-336`) and `_try_image_enrichment`'s `node_count` and character-floor checks (`verdict.py:242-249`).
7. Unit tests SHALL verify that a flat document whose tree had `max_leaf_ratio=0.86` and whose flat blocks have a materially different ratio receives the flat value.
8. This deliverable SHALL be treated as verdict-distribution-affecting and gated accordingly (Requirement 9).

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
5. `CURRENT_PIPELINE_VERSION` (`config.py:15`) SHALL be bumped from 4 to 5 in the same commit as the first merged change that can reclassify the corpus, per RFC-014 D3. Remote Docling re-baselining does not apply (see Environment).
6. The corpus gate SHALL be coordinated with RFC-041 task 3.5a, which owns the full-corpus verdict-diff baseline.

### Requirement 10: Zone 2 Closure — post-NFKC ScriptContext call sites (D10)

**User Story:** As the garble detector, I want every `ScriptContext` I am given to have been constructed before NFKC destroyed the evidence, so that my verdicts are based on signals that still exist.

Adopted with Zone 2 ownership (OQ2). RFC-040 D6 reordered NFKC-before-bidi only in `_pre_inference_normalize`, leaving seven post-NFKC `ScriptContext` construction sites broken — the "RFC-040 Zone 2 pattern" the ownership manifest exists to catch.

#### Acceptance Criteria

1. Each of the seven post-NFKC `ScriptContext` call sites SHALL be enumerated by file and line, and each SHALL be either corrected to construct its context pre-NFKC or documented as genuinely unaffected with the reason recorded.
2. No `ScriptContext` used for garble or bidi decisions SHALL be constructed from text that has already been NFKC-normalized, unless the signal it carries is provably NFKC-invariant.
3. An architecture guard SHALL enforce criterion 2 so the pattern cannot silently reappear — this is its third recurrence (RFC-040 D6, RFC-045 `2c39168`, and D5 of this RFC all addressed different instances of it).
4. `audit/zones/ZONE_OWNERSHIP.yaml` `zone_2.successor_rfc` SHALL be set to RFC-046, and `zone_2.resolved` SHALL be set true only when criteria 1–3 hold.
5. D10 SHALL land in Wave 3 alongside D5, which shares its subsystem.

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

## Implementation Plan


### Phase naming across the three artifacts

Three vocabularies are in use and they describe the same work. This table is the translation; it appears identically in the plan, the RFC and the tasks file.

| Plan (§6) | RFC (Implementation Plan) | Tasks | Deliverables |
|---|---|---|---|
| **P0** · Baseline truth | Phase 0 — Baseline | **Wave 1** | D2, D3, RFC-042 4.2 |
| **P0** (evidence arm) | Phase 1 — Evidence | **Wave 2** | D1 |
| **P0.5** · Cluster fixes | Phase 2 | **Wave 3** | D5, D8, D10 |
| **P0.5** | Phase 3 | **Wave 4** | D6 |
| **P0.5** | Phase 4 | **Wave 5** | D7 |
| **P0.5** | Phase 4 | **Wave 6** | D4 |
| *(beyond plan §6)* | Phase 5 | **Wave 7** | D9 |
| *(beyond plan §6)* | — | **Wave 8** | RFC-047 go/no-go |

In short: **P0 = Waves 1–2, P0.5 = Waves 3–6.** Waves 7–8 are the corpus validation and the RFC-047 decision, which the plan's §6 framing did not cover. RFC-046 scopes P0 and P0.5 only; the plan's P1–P4 belong to RFC-047 if it is written.

### Sequencing

1. **Phase 0 — Baseline** (D2, D3, and RFC-042 4.2 per D9). Attribution must exist before anything can move, or movements cannot be explained. Corpus run at the end of this phase is the graded baseline.
2. **Phase 1 — Evidence** (D1). Independent of the code work; parallelizable with Phase 0. Gates any RFC-047 claim, not this RFC's own deliverables.
3. **Phase 2 — Independent cluster fixes** (D5, D8, D10). D5 and D10 share the garble/normalization subsystem and land together; D8 is independent and runs report-only (OQ5).
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
| **Total** | | **~59h** | **(revised 2026-09-15 from ~51h: +D10 via Zone 2 ownership, OQ2)** |

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
- **Corpus:** Attributed per-document delta table, both directions, after Phase 4. Baseline taken after Phase 0.

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

## Open Questions

All five original open questions were put to the owner on 2026-09-15 and answered. They are retained with their resolutions rather than deleted, so the reasoning behind each decision stays on the record.

1. **Should RFC-042 task 4.2 be adopted here, or should RFC-046 sequence behind RFC-042?** — **RESOLVED (2026-09-15): adopt it here.** D2 and D8 both add config surface, and 4.2 is the guard against repeating the double-sourcing and parse-asymmetry defect class this RFC is fixing. Blocking on RFC-042's other 13 tasks — including the `PROMOTION_ORDER` work this RFC does not need — was rejected. Task 1.7 stays in Wave 1.
2. **Who owns Zone 2 after D5?** — **RESOLVED (2026-09-15): RFC-046 takes full successor ownership.** `successor_rfc` transfers from RFC-041 to RFC-046 in `audit/zones/ZONE_OWNERSHIP.yaml`. This deliberately grows scope: RFC-046 becomes accountable for the seven post-NFKC `ScriptContext` call sites RFC-040 D6 left broken, which are now **Requirement 10 / D10**, landing in Wave 3 alongside D5.
3. **Does Requirement 7 criterion 2 need a Hard Rule #5 ruling?** — **RESOLVED (2026-09-15): yes, keep the better extraction.** Storing ~30,000 correct characters under an honest FAIL is consistent with Hard Rule #5, because the tree is still called bad; only the text underneath improves. Task 5.2 proceeds as specified. No additional sidecar divergence marker was required.
4. **How far should D6's contract change go?** — **RESOLVED (2026-09-15): the thorough path.** `evaluate_gates` gains a caller-declared signal source so a dead argument becomes impossible to pass, and a guard test enforces it. This touches every caller, accepted deliberately to prevent recurrence rather than to minimise blast radius.
5. **Is the density numerator (D8) in scope, or is it threshold work by another name?** — **RESOLVED (2026-09-15): in scope, but measure-first.** The corrected counter is built and run in **report-only mode**; it produces a per-document table showing exactly which documents would move and by how much, and activation is a separate decision taken on those real numbers. Task 3.4 is restructured accordingly. This removes the RFC author's estimate from the decision entirely and is a better shape than the original proposal.

### Still open

6. **Where do the 25 corpus documents come from?** The Run-8 artifacts and four of the six failing documents are not in the shared bucket (see Environment below). The owner is supplying the corpus. Until it arrives, D5 and D7 are validatable against Docs 17 and 22; D4, D6 and D8 have unit coverage only.

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
| Process: pipeline version | `config.py:12-15`; compared `client/remote.py:62` |
