# Architecture Defect Zones Audit — 2026-09-02 POST-RFC043

**Date:** 2026-09-02  
**Run:** POST-RFC043  
**Scope:** Critical architectural defect patterns across 7 interdependent zones

---

## Summary Table

| # | Zone | Severity | Bug Count | Key Files | Status |
|---|---|---|---|---|---|
| 1 | OCR Pipeline Decision & Recovery Cascade | Critical | 12 | picture_plane.py, pictures.py, recovery.py, indexer.py, gates.py | Audited |
| 2 | Garble Detection NFKC Signal Destruction | Critical | 8 | garble.py, script.py, normalize.py, gates.py | Audited |
| 3 | Table-Unaware Pre-Tree Text Transforms | High | 7 | tree_split.py, headings.py | Audited |
| 4 | Verdict Promotion & Hard-Rule-5 Bypass Cascade | High | 7 | verdict.py, gates.py, config.py | Audited |
| 5 | Verdict Persistence Dual-Writer & Hysteresis Fragility | High | 6 | verdict.py, registry_mirror.py, queries.py, documents.py | Audited |
| 6 | Gate-to-Recovery Dispatch Wiring Gap | High | 6 | gates.py, recovery.py, indexer.py | Audited |
| 7 | Remote/Local Execution Divergence & Config Snapshot Leak | Medium | 4 | indexer.py, config.py, normalize.py, docling_conv.py, remote.py | Audited |

**Total Attributed Bugs:** 50+ across 7 zones spanning RFC-018 through RFC-043

---

## Zone Details

### Zone 1: OCR Pipeline Decision & Recovery Cascade

**Severity:** Critical | **Bug count:** 12

#### Mechanism

Multiple interacting OCR decision surfaces make independent, stateful decisions that suppress or conflict with each other. Three structural causes generate this zone:

1. **Multiple independent OCR decision sites** make contradictory verdicts:
   - `decide_ocr_strategy` in picture_plane.py
   - `_text_layer_has_content` in pictures.py
   - `force_full_page` in indexer.py
   - per-picture OCR in the converter chain

   Fixing one site's false-negative creates a false-positive at another (forced page-level OCR zeroes per-picture enrichment; marker-count duplication unconditionally triggers per-picture OCR on already-clean text).

2. **The re-entry guard (`full_page_already_applied`)** is a cross-call mutable flag that makes the decision tree order-dependent:
   - UNIFIED_OCR_PLAN_ENABLED branch short-circuited the guard until RFC-042 reordered them
   - Recovery methods must explicitly set the flag after recovery returns True
   - The ordering is load-bearing and documented only in comments

3. **Four recovery methods are fully implemented but never called**:
   - `_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_image_dominant_ocr`, `_recover_vlm_fallback` 
   - Declared in GateSpec.recovery_fns as string references
   - Import-time assertions verify they exist; runtime dispatcher in `_convert_to_tree` never invokes them
   - Trace analysis confirms zero production callers for all four methods

#### History

- **Chain 1** (RFC-018→019→020→021→022→024→025): Page-coverage filter + forced OCR zeroed picture enrichment on docs 3,9; stripped heading structure on docs 7,17,20,21; required 5 further fixes, cascade continued through 7 RFCs.
- **Chain 2** (RFC-042→043): UNIFIED_OCR_PLAN_ENABLED branch bypassed re-entry guard; severity escalated from high to critical between 08-24 and 08-26 audits.
- **Chain 3** (ongoing): Client.py marker-count-duplication creates duplicate PictureResults causing unconditional per-picture OCR regardless of existing clean text.
- **Chain 15** (RFC-041): Text-layer probe (_text_layer_has_content) never executes when upstream page-level short-circuit skips clip_text.
- **Chain 16** (RFC-041): Per-picture OCR conflated with page-level OCR escalation, reclassifying prose blocks into image-block ocr_text fields invisible to content_class computation.
- **Chain 21** (RFC-041 post-verify): Four top-level OCR recovery dispatch methods discovered as dead code with zero production callers.

#### Code Evidence

```python
# decide_ocr_strategy (picture_plane.py:357-430)
# full_page_already_applied guard runs FIRST (line 389), before UNIFIED_OCR_PLAN_ENABLED (line 404)
if state.full_page_already_applied:
    return OcrStrategy.SKIP
if unified_enabled:  # UNIFIED_OCR_PLAN_ENABLED branch
    # This ordering is load-bearing; comment documents prior bypass

# _recover_garble_ocr (recovery.py:400-432) — fully implemented, callers=0
def _recover_garble_ocr(...):
    ...
    _execute_ocr_retry(...)

# GATES table (gates.py:354-441)
GateSpec(
    defect=Defect.GARBLING,
    recovery_fns=('_recover_garble_ocr','_recover_vlm_fallback'),
    recovery_eligible=_eligible_garble,
    ...
)
# Import-time assertions (lines 464-489) verify these exist
# Runtime dispatcher (_convert_to_tree) never reads recovery_fns to call them

# _text_layer_has_content (pictures.py:232-267)
# Depends on upstream clip_text execution, which can be skipped by page-level short-circuit
```

#### Key Files

- src/pageindex_mcp/picture_plane.py
- src/pageindex_mcp/converters/pictures.py
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/helpers/gates.py

---

### Zone 2: Garble Detection NFKC Signal Destruction

**Severity:** Critical | **Bug count:** 8

#### Mechanism

NFKC Unicode normalization destroys the presentation-form signal that downstream garble and bidi-coherence gates depend on. Four structural causes:

1. **Signal destruction via normalization**:
   - `_pre_inference_normalize` (normalize.py) captures `had_presentation_forms` BEFORE NFKC
   - Attaches flag to RtlDecision
   - Only reaches gates that consume RtlDecision
   - Multiple independent ScriptContext construction sites (4+) must independently replicate the signal inference
   - Each new site is a potential signal-loss point

2. **Compensating mechanism proliferation**:
   - `detect_garble` has fallback (lines 584-592) that infers `had_presentation_forms=True` when dominant_script is Arabic but zero forms survive
   - Fallback only covers callers that pass script_context
   - Multiple independent call sites construct ScriptContext with `had_presentation_forms=False`, bypassing the safety net

3. **Digit-ratio blind spot for short garbled text**:
   - `digit_ratio` prong only fires above `garble_digit_floor` (default 500 chars)
   - Secondary `numeric_junk_short` prong (>= 50 chars, > 90% digits) partially closes gap
   - Was added reactively, not part of original design

4. **Latin-gibberish unreachable for project's validation vertical**:
   - Requires `expected_script` non-None
   - `_script_from_filename` returns None for German filenames
   - German T&Cs are primary validation vertical

#### History

- **Chain 4** (RFC-028 D2, RFC-033 D2, RFC-034 D7): NFKC destroys presentation-form codepoints independently rediscovered three times; structurally present as of 2026-08-26.
- **Chain 5** (RFC-024→025): `_check_bidi_coherence` has 0% true-positive rate due to two independent, each-sufficient null detectors; BIDI_COHERENCE_ENFORCE promoted to default-true on reasoning that zero violations meant zero risk.
- **Chain 18** (RFC-018→019): Digit-ratio only fires above 500-char floor (ISS-36); latin_gibberish unreachable for German filenames.
- **Chain 19** (RFC-020): `short_text_prior_garble` short-circuit made `detect_garble` non-idempotent; prior verdict permanently poisons subsequent evaluations.
- **Chain 28** (RFC-041/042/043): Zone 2 NFKC ownership unresolved across RFC cycle despite being flagged as critical blocker.

#### Code Evidence

```python
# _pre_inference_normalize (normalize.py:138-170)
# Captures signal BEFORE NFKC
had_pres_forms = any('fb50' <= ch <= 'fdff' or 'fe70' <= ch <= 'feff' 
                     for ch in text)
text = unicodedata.normalize('NFKC', text)
# Attaches to RtlDecision, only consumed by gates that use it

# detect_garble (garble.py:529-614, lines 584-592)
# Compensating fallback only when script_context passed
if script_context.had_presentation_forms:
    # Use it
else:
    # Compensating fallback: infer from script + content
    if dominant_script == 'Arab' and arc_count > 0 and pres_count == 0:
        had_presentation_forms = True

# _garble_prongs (garble.py:339-440)
# Digit-ratio blind spot for short text
if len(norm) > cfg.garble_digit_floor:  # default 500
    # digit_ratio prong fires
# numeric_junk_short (lines 401-408) partially closes for >= 50 chars at > 90%

# latin_gibberish (lines 418-434)
# Unreachable for German (expected_script is None)
if expected_script and ratio > threshold:
    # fires only for non-German
```

#### Key Files

- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/script.py
- src/pageindex_mcp/converters/normalize.py
- src/pageindex_mcp/helpers/gates.py

---

### Zone 3: Table-Unaware Pre-Tree Text Transforms

**Severity:** High | **Bug count:** 7

#### Mechanism

Multiple independent pre-tree text transforms each independently fracture pipe-tables because they share no common table-boundary primitive. The structural defect:

- `compute_table_spans` and `line_in_table_span` exist in tree_split.py
- ARE wired into all three heading injectors in headings.py
- Are NOT wired into `split_oversized_leaf_nodes` in the SAME FILE
- `split_oversized_leaf_nodes` applies four fallback splitting strategies that match patterns INSIDE table rows
- No strategy calls `compute_table_spans` or `line_in_table_span` to check if a split point falls inside a pipe-table

Each fix to one transform collaterally breaks documents handled by another transform:
- Heading injection blocking richer flat fallback (RFC-028 D1) → 80% content loss
- `_strip_toc_heading_nodes` over-stripping depth (RFC-033 D11)
- Landscape chart label shattering (RFC-035 D2)

#### History

- **Chain 13** (RFC-005→010→028→029→033→034→035→036): RFC-005 introduced `split_oversized_leaf_nodes` with no table guard. Downstream collisions: RFC-010 D4 (marsoom 33: 125→58 nodes), RFC-028 D1 (80% content loss), RFC-029 D4 (Schedule 1-5 destroyed), RFC-033 D11 (depth 3→2), RFC-034 D16/D20 (marsoom 13 depth 4→2), RFC-035 D2 (71+ singleton axis labels).

#### Code Evidence

```python
# split_oversized_leaf_nodes (tree_split.py:398-474)
# Four fallback strategies with NO table-boundary check
def split_oversized_leaf_nodes(lines, ...):
    for strategy in [_split_on_atx_headings,
                     _split_on_generic_numbered_lines,
                     _split_on_paragraph_markers,
                     _split_on_blank_line_paragraphs]:
        # Each strategy matches patterns INSIDE table rows
        # No call to compute_table_spans or line_in_table_span

# compute_table_spans (tree_split.py:485-507)
# Exists, used in headings.py, not in split_oversized_leaf_nodes
def compute_table_spans(lines):
    # Scans for contiguous pipe-table spans
    return list[tuple[int, int]]  # callers=0 per trace_path

# line_in_table_span (tree_split.py:509-510)
# Used by all three heading injectors in headings.py
def line_in_table_span(idx, spans):
    return any(lo <= idx < hi for lo, hi in spans)

# _inject_arabic_structural_headings (headings.py:143-160) DOES use guard
# _inject_german_clause_headings (headings.py:230-250) DOES use guard
# _inject_english_article_headings (headings.py:262-280) DOES use guard

# But split_oversized_leaf_nodes in tree_split.py does NOT
```

#### Key Files

- src/pageindex_mcp/helpers/tree_split.py
- src/pageindex_mcp/converters/headings.py

---

### Zone 4: Verdict Promotion & Hard-Rule-5 Bypass Cascade

**Severity:** High | **Bug count:** 7

#### Mechanism

The verdict classification pipeline (evaluate_gates → apply_promotions → classify_verdict) has multiple interacting bypass paths that override Hard-Rule-5 ('never silently persist a low-quality tree'). Four structural causes:

1. **First-match-wins if/elif chain**:
   - `apply_promotions` evaluates six _try_* helpers in source-code order
   - VG-6 fix now evaluates ALL paths for telemetry
   - Winner remains first match, making ordering load-bearing

2. **D1 structural hard-fail exception**:
   - `max_leaf_ratio > threshold` is unconditional hard-fail
   - Except when image enrichment exists: returns PASS via `_apply_clamp`
   - Bypasses what would be unconditional FAIL
   - VG-7 ensures `_ie` computed once and shared between D1 and D2

3. **Zero-content early-return bypass**:
   - `evaluate_gates` (lines 174-183) emits hard_fail before any gate evaluation
   - Bypasses recovery dispatch entirely
   - Zero-content documents classified FAIL without gate consultation

4. **Threshold widening without empirical anchoring**:
   - PASS_MAX_LEAF_RATIO widened three times (0.17→0.20→0.30)
   - Chased jitter on different documents
   - Masks extraction defects rather than fixing them
   - Violates Hard-Rule-5 by design

#### History

- **Chain 7** (RFC-023 D10→RFC-024 D0→RFC-025 D0): PASS_MAX_LEAF_RATIO widened; Haftpflicht with 81/132 garbled nodes passed at 0.20; hysteresis defeated by reingestion wipe.
- **Chain 8** (RFC-023→RFC-024→RFC-025): Image-enrichment bypass evolved from implicit drift to explicit priority=100 escape hatch; near-zero-content docs (marsoom 13: 2 blocks/38 chars) earn PASS with no content-validity check.
- **Chain 17** (RFC-022 B3): Table blocks carry no 'text' key by design; char-count diagnostics see 0 chars for correctly-structured content — measurement artifact mistaken for regression.
- **Chain 20** (RFC-041→RFC-042): Zero-content early-return bypass discovered; three original claims stale but NEW gap identified.
- **Chain 23** (RFC-025/026): Threshold changes downgrade verdicts without fixing underlying extraction failures.

#### Code Evidence

```python
# evaluate_gates (verdict.py:126-224, lines 174-183)
# Zero-content early-return BEFORE recovery dispatch
if sig.node_count == 0 or len(sig.flat_text.strip()) == 0:
    return GateOutcome(
        ...,
        hard_fail_verdict=VerdictResult("FAIL","zero_content",...)
    )
    # Exits before any HARD_FAIL_DEFECTS or recovery check

# apply_promotions (verdict.py:405-580)
# First-match-wins with D1 image-enrichment exception
if sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio:
    if _ie is not None:
        return _apply_clamp(_ie, _is_image_enrichment=True)
    # Otherwise implicit FAIL from hard_fail_verdict

# D2 ordered pipeline (lines 541-576)
_matches = []  # Built in source-code order
for _try in [_try_image_enrichment, _try_structural_pass, ...]:
    if _try(...):
        _matches.append(...)
# Return _matches[0]  # First match wins

# HARD_FAIL_DEFECTS (gates.py:492)
frozenset(g.defect for g in GATES if g.hard_fail)
# GARBLING, REORDERED, EMPTY_NODE_CONTAMINATION, LOW_CONTENT_DENSITY, SUSPECT_DENSITY
```

#### Key Files

- src/pageindex_mcp/helpers/verdict.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/config.py

---

### Zone 5: Verdict Persistence Dual-Writer & Hysteresis Fragility

**Severity:** High | **Bug count:** 6

#### Mechanism

Three distinct verdict-persistence writers with different consistency models create structural fragility. Three causes:

1. **Asymmetric consistency design**:
   - `save_doc_meta` (storage/verdict.py:78-198): consistency_model='eventual', no _confirm_write_visible barrier, no CAS priority comparison
   - Postgres _UPSERT_SQL: VERDICT_PRIORITY-based CAS (overwrites only when priority >= existing)
   - MinIO sidecar backfill has no CAS guard
   - These stores can transiently disagree; convergence depends on best-effort backfill

2. **Silent failure in persistence layer**:
   - `_upsert_registry_row` (registry_mirror.py:136-315) returns False on every degraded path but never raises
   - Until RFC-042 D3, callers had no signal to distinguish success from failure
   - `reconcile._drain_verdict_retry_queue` unconditionally deleted retry keys regardless
   - Permanently lost verdicts on transient failures

3. **Hysteresis ledger destruction**:
   - Hysteresis (RFC-025 D0) reads prior verdict from processed/*.meta.json
   - Standard corpus reingestion wipes this store
   - Destroys the ledger that `find_prior_verdict` scans
   - Causes verdict flapping independent of content changes

#### History

- **Chain 11** (RFC-022→RFC-025): Write-visibility barrier over-provisioned at 4.4s caused PersistenceNotVisibleError; removed from save_doc_meta but retained in save_doc/save_flat_doc (asymmetry). MinIO sidecar backfill has no CAS guard; on failure sidecar stays stale until reconcile_registry_drift heals.
- **Chain 12** (RFC-025 D0→RFC-026): Reingestion wipes processed/*.meta.json, destroying hysteresis ledger; GHV-TKV-Tarif flapped PASS→MARGINAL on identical tree.
- **Chain 22** (RFC-042 D3): Discovered 10+ direct save_doc_meta bypass callers (indexer.py, documents.py, verdict.py, preprocess_client.py, promotion_sweep.py, backfill.py, reconcile.py) contradicting single-writer claim; consolidated.
- **Chain 26** (RFC-042 D3): Reconcile retry loop silently dropped verdicts on transient Postgres failures because _upsert_registry_row never raised; added bool return and retention logic.

#### Code Evidence

```python
# save_doc_meta (storage/verdict.py:78-198)
# Eventual consistency with no barriers or CAS
def save_doc_meta(...):
    consistency_model='eventual'
    # No _confirm_write_visible call
    # Unconditional merge of verdict/verdict_reason/max_leaf_ratio

# _upsert_registry_row (registry_mirror.py:136-315)
# Silent failure swallows exceptions
def _upsert_registry_row(...) -> bool:
    try:
        # Postgres write
        return True
    except:
        REGISTRY_WRITE_FAILURES_TOTAL.inc()
        enqueue_verdict_retry(...)
        return False  # Never raises

# Postgres _UPSERT_SQL (registry/queries.py:24-38)
# Priority-based CAS
CASE
  WHEN VERDICT_PRIORITY[incoming] >= VERDICT_PRIORITY[existing]
    THEN (incoming verdict)
  ELSE (existing verdict)
END

# _confirm_write_visible (storage/minio_ops.py:37-58)
# Used by save_doc/save_flat_doc but explicitly NOT by save_doc_meta
```

#### Key Files

- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/worker/registry_mirror.py
- src/pageindex_mcp/registry/queries.py
- src/pageindex_mcp/storage/documents.py

---

### Zone 6: Gate-to-Recovery Dispatch Wiring Gap

**Severity:** High | **Bug count:** 6

#### Mechanism

The GATES table declares recovery functions and eligibility predicates, import-time assertions verify completeness, but the runtime dispatcher never calls them. Two structural causes, plus a recurring pattern:

1. **Declarative specification vs runtime execution disconnect**:
   - GATES list (gates.py:354-441) has import-time exhaustiveness assertions (lines 456-489)
   - Every gate with RETRY_OCR/RETRY_RTL policy must have recovery_fns and recovery_eligible
   - These assertions pass, creating false sense of completeness
   - `_convert_to_tree` (indexer.py:443-963) calls `finalize_gate_and_route`, evaluates gates
   - Never iterates GateSpec.recovery_fns to invoke the declared recovery methods

2. **'Fixed but never wired' pattern**:
   - Correct implementations exist in working tree but are inert in production
   - `chunked_docling_timeout_s` (RFC-027 task 4.2) marked complete, never wired to worker.py
   - `_check_bidi_coherence` improvements staged but inactive
   - RFC-030 D6 judge-calibration rules committed but never called
   - RFC-034 D19 enrichment-displacement guard staged but inactive

3. **New failure reasons never routed to recovery**:
   - RFC-029 added four new validate_tree failure reasons to GATE_TABLE
   - Never wired into client.py recovery routing
   - Caused 3 documents PASS→ERROR in Run 13 — "single highest-impact systemic bug of that run"
   - Also: validate_tree returns FIRST firing gate by severity; NODE_COUNT_LOW (severity=1) masks NODE_GARBLING (severity=3)

#### History

- **Chain 9** (RFC-029→Run 13): Four new validate_tree failure reasons added to GATE_TABLE but never wired into recovery routing. Caused 3 documents PASS→ERROR. Also, validate_tree returns only first gate, routing low-severity reasons to wrong recovery.
- **Chain 10** (RFC-027→RFC-030→RFC-034): Four instances of 'fixed but never wired' — chunked_docling_timeout_s, _check_bidi_coherence improvements, RFC-030 D6 judge-calibration, RFC-034 D19 enrichment-displacement guard.
- **Chain 21** (RFC-041→RFC-043): Four recovery methods implemented but _convert_to_tree dispatcher never updated, leaving OCR recovery cascade orphaned.
- **Chain 27** (RFC-042 Zone 1): Confirmed gate coupling claims stale but discovered zero-content early-return bypass as new gap.

#### Code Evidence

```python
# GATES table (gates.py:354-441)
# Declares recovery_fns but runtime never calls them
GateSpec(
    defect=Defect.GARBLING,
    recovery_fns=('_recover_garble_ocr','_recover_vlm_fallback'),
    recovery_eligible=_eligible_garble,
    ...
)
GateSpec(
    defect=Defect.NODE_COUNT_LOW,
    recovery_fns=('_recover_low_content_ocr','_recover_image_dominant_ocr'),
    ...
)
# Import-time assertions (lines 464-489) verify these are non-empty

# _recover_garble_ocr (recovery.py:400-432)
# Fully implemented, callers=0 per trace_path
def _recover_garble_ocr(...):
    ...
    _execute_ocr_retry(...)

# _convert_to_tree (indexer.py:443-963)
# Evaluates gates but never reads recovery_fns
def _convert_to_tree(...):
    gate_result = finalize_gate_and_route(...)
    # Never:
    # for fn_name in gate_result.recovery_fns:
    #     fn = getattr(recovery_module, fn_name)
    #     fn(...)
```

#### Key Files

- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/client/indexer.py

---

### Zone 7: Remote/Local Execution Divergence & Config Snapshot Leak

**Severity:** Medium | **Bug count:** 4

#### Mechanism

Fixes in the local working tree have zero effect on remote Scaleway Docling microservice, which runs a stale deployed image. Compounded by config snapshot violations where hot-path files read os.environ instead of frozen PipelineConfig. Three structural causes:

1. **Remote/local divergence**:
   - Scaleway Docling microservice runs stale deployed image (built 2026-07-30..08-04)
   - Predates multiple local fixes
   - No parity mechanism between local converter and remote service
   - BiDi heading-reversal guard (_heading_is_logical_order) found in zero git commits
   - Remote Arabic documents still get headings reversed
   - This is an architectural gap with no safety net

2. **Config snapshot leak**:
   - `CLIENT_BUILD_SHA` and `PRE_GARBLE_FORCE_OCR_ENABLED` read from os.environ in indexer.py hot paths
   - Should be frozen PipelineConfig fields
   - RFC-042 D4 hoisted them, but pattern persists elsewhere
   - No automated guard beyond TestHotPathConfigAccessGuard test

3. **Timeout calibration without empirical basis**:
   - RFC-032 D3 set 3x timeout multiplier (assumed 3-10x slowdown)
   - Actual measured range 2.32x-11.00x
   - Recalibrated to 16.5x (RFC-032 D9)
   - Entangled with chunked_docling_timeout_s never wired to worker.py

#### History

- **Chain 6** (RFC-033 D2→RFC-041): Heading-reversal guard implemented locally, never committed (git log -S finds zero commits). Remote Scaleway Docling runs stale 2026-07-30..08-04 image — remote Arabic documents still reversed.
- **Chain 14** (RFC-032 D3→RFC-032 D9→RFC-027 task 4.2): Timeout multiplier 3x uncalibrated; actual range 2.32x-11.00x; recalibrated to 16.5x. Entangled with chunked_docling_timeout_s marked complete but never wired to worker.py (world-stats-pocketbook timed out across 3 consecutive runs).
- **Chain 25** (RFC-042 D4): Discovered CLIENT_BUILD_SHA and PRE_GARBLE_FORCE_OCR_ENABLED still read from os.environ in indexer.py hot paths, not frozen PipelineConfig fields.

#### Code Evidence

```python
# _pre_inference_normalize (normalize.py:138-170)
# BiDi reconstruction runs locally only; remote service runs deployed version
def _pre_inference_normalize(text, ...):
    # Captures presentation_forms BEFORE NFKC
    # Builds RTL reconstruction — only local

# _convert_to_tree (indexer.py:443-963, line 541)
# Config snapshot leak — reads os.environ instead of frozen config
PRE_GARBLE_FORCE_OCR_ENABLED = os.environ.get(...)  # WRONG
# Should be: pipeline_config.pre_garble_force_ocr_enabled  # FIXED in RFC-042 D4

# probe_conversion_route (docling_conv.py:370-411)
# Scanned/image classification with timeout multiplier
# RFC-032 D3: 3x (uncalibrated)
# RFC-032 D9: 16.5x (measured range 2.32x-11.00x)
def probe_conversion_route(...):
    multiplier = 16.5  # Now measured-based
    timeout = base_timeout * multiplier

# _remote_pdf_to_markdown
# Sends expected_script in payload but no version enforcement
# Remote service may not recognize or process it
def _remote_pdf_to_markdown(...):
    payload = {..., 'expected_script': ...}
    # No check: does remote service handle expected_script?
```

#### Key Files

- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/config.py
- src/pageindex_mcp/converters/normalize.py
- src/pageindex_mcp/converters/docling_conv.py
- src/pageindex_mcp/client/remote.py

---

## Cross-Cutting Themes

1. **Sequential remediation chains, not one-shot fixes**: Nearly every RFC's fix becomes the next RFC's root-cause finding (RFC-018→019→020 picture-OCR filter composition; RFC-021→022→023 verdict-gate/routing chain; RFC-024→025→026 leaf-ratio threshold saga; RFC-027→028→029→030 Arabic-recovery cascade; RFC-033→034→035→036 landscape/write-barrier cascade).

2. **'Fixed but never wired/committed' is a recurring, distinct failure class** separate from logic bugs: chunked_docling_timeout_s, _check_bidi_coherence improvements, RFC-030 D6 judge-calibration rules, and RFC-034 D19's enrichment-displacement guard were each correct in isolation but inert in production because nothing called or committed them.

3. **Parameter/reason-string threading gaps** make otherwise-correct detectors unfireable: expected_script never passed to garble callers (RFC-019 D2), node_garbling never recognized by OCR-escalation conditional (RFC-018 D3b), RFC-029's four new validate_tree failure reasons never wired into recovery routing (Run 13's highest-impact systemic bug).

4. **NFKC Unicode normalization is a recurring, independently-rediscovered blind spot** for Arabic/RTL quality gates: it silently destroys the presentation-form signal that both the garble detector and the bidi-coherence detector key on, independently found in RFC-028 D2, RFC-033 D2, and RFC-034 D7, confirmed still structurally present as of 2026-08-26.

5. **Threshold-widening without true anchoring** produces recurrence cycles rather than convergence: PASS_MAX_LEAF_RATIO was widened three times (0.17→0.20→0.30) chasing jitter on different documents, and even the eventual hysteresis fix (RFC-025 D0) was defeated by an orthogonal issue (corpus reingestion wiping the prior-verdict ledger).

6. **Gates/mechanisms actively fighting each other**: page-coverage OCR-skip filter vs per-picture forced-OCR (chart garbling vs scanned-page recovery); full_page_already_applied re-entry guard vs newer UNIFIED_OCR_PLAN_ENABLED branch that bypasses it; digit-ratio garble check vs its own 500-char floor duplicated in two non-shared functions; page-level OCR escalation vs per-picture OCR conflating the same recovered text into different block types.

7. **Verdict-promotion / Hard-Rule-5 bypass is a persistent, worsening surface**: it evolved from implicit threshold-widening drift into an explicitly hard-coded, priority=100 escape hatch (image_enrichment_promoted) that outranks structural hard-fail verdicts by design.

8. **Null/zero-sensitivity detectors get misread as 'safe' rather than 'broken'**: _check_bidi_coherence measured 0% true-positive rate (structurally excluded from its own line-selector's sampled range), yet BIDI_COHERENCE_ENFORCE was promoted to default-true on the reasoning that zero violations meant zero risk, rather than recognizing the detector could not fire at all.

9. **Table/structural-integrity destruction caused by multiple independent pre-tree text transforms** sharing no common table-boundary primitive: Arabic heading injection, the ordinal-matching oversized-leaf splitter, and _repair_docling_tables' degenerate-row collapse each independently fracture pipe-tables because compute_table_spans/line_in_table_span exists in tree_split.py but is only wired into headings.py, never into the splitter in the same file.

10. **Dual-store/dual-CAS divergence for verdicts**: MinIO sidecar vs Postgres registry, each with its own priority map and CAS guard, until Postgres was designated sole arbiter — but even that fix leaves the MinIO sidecar without an equivalent CAS guard, so a failed best-effort backfill can still silently disagree with the authoritative store.

11. **Remote-vs-local execution divergence is an emerging theme** distinct from pure code bugs: fixes that exist correctly in the local working tree (uncommitted) have zero effect on documents processed via the remote Scaleway Docling microservice, which runs a stale deployed image; this pattern recurred for the BiDi heading guard and was separately flagged as an unscoped zone.

12. **Corpus-audit scope narrowing is sometimes mistaken for remediation**: zones get marked 'closed' purely because their key files fell outside a later audit's scope, not because the underlying defects were confirmed fixed — an audit-methodology risk distinct from the code defects themselves.

13. **Measurement/scoring blind spots masquerade as content-loss regressions**: table blocks carry no 'text' key by design, so any diagnostic (including the audit's own char-count tooling) that reads block.get('text','') silently undercounts real content — turning a measurement artifact into a false regression signal (RFC-022 B3 / GHV-TKV-Tarif).

14. **AGPL exposure (Hard Rule 4) narrowing has been incremental and incomplete**: removing the direct PyMuPDF dependency left pymupdf4llm's transitive pull open as a live fallback path that can fire silently on remote-Docling timeouts, with insufficient logging to confirm or exclude whether it fired on a given document.

15. **Gate and recovery eligibility predicates couple/decouple/re-couple**: promotion bypass → OR-gate coupling introduced → OR-gate removed (RFC-043 D2) but indirect flag coupling persists; plus newly-discovered zero-content early-return bypass that gates never reached.

16. **Audit staleness vs validation**: RFC-041 post-verify audits show most zone claims already fixed or refuted; RFC-042 Zone 1 validation confirms pattern — audit-generated critical findings frequently stale, validation-before-remediate prevents wasted effort but also delays discovery of real gaps (zero-content early-return, retry-loop persistence).

17. **Dead code and unwired implementations**: OCR recovery cascade (4 methods) implemented but zero production callers; verdict persistence write-through pattern defined but 10+ bypass callers remained active; recovery eligibility machinery fully implemented but bypassed by zero-content early-return in dispatcher.

18. **Write barrier and dual-writer enforcement gaps**: single-writer pattern claimed but 10+ direct save_doc_meta callers active; _upsert_registry_row return-value never checked until RFC-042 D3, allowing silent verdict loss on transient Postgres failures; config snapshot freeze claimed but os.environ reads persisted in hot paths.

19. **Compliance deferred but never resolved**: Zone 5 HR4 (AGPL bypass via bare RETRY continue) explicitly deferred as escalation, not fixed; Zone 2 NFKC critical ownership explicitly unresolved at Wave 1 checkpoint; both remain open across multiple RFC cycles.

20. **Verdict gate mistuning masks extraction defects**: RFC-025/026 threshold changes and promotion-flag gates downgrade verdicts without fixing underlying extraction failures (zero-content Arabic PDFs, Unfallversicherung 15x character loss); gate-hardening Run 10 correctly re-grades but extraction defects persist unfixed.

21. **Configuration and environment isolation failures**: CLIENT_BUILD_SHA, PRE_GARBLE_FORCE_OCR_ENABLED read from os.environ in hot paths rather than frozen PipelineConfig; ScriptContext presentation-forms wiring not fully parameterized despite RFC-043 D3 attempt.

---

## Audit Methodology Notes

This audit identifies structural defect zones rather than isolated bugs. Each zone describes:

- **Mechanism**: The architectural root cause(s) enabling the bug class to recur
- **Evidence History**: A chain analysis showing how the zone manifests across multiple RFCs
- **Code Evidence**: Specific locations and code patterns that instantiate the mechanism

The 50+ attributed bugs are not duplicates; they are distinct manifestations of 7 deeper structural defects. Fixing individual bugs without addressing the zone mechanism causes recurrence or collateral drift.

The cross-cutting themes document patterns that span multiple zones and suggest systemic issues in how changes are validated, deployed, and monitored.

---

## Simplification Proposals

### OCR Pipeline Decision & Recovery Cascade

1. **CORE SIMPLIFICATION**: Collapse the four independent OCR decision sites into a single ordered decision table evaluated once per node (image/page), and make the recovery dispatcher actually consult GateSpec.recovery_fns via getattr() instead of leaving it a declarative dead-letter. Replace the mutable cross-call `full_page_already_applied` flag with a single-pass state object scoped to one OCR decision (computed once, never mutated mid-decision), so ordering between the guard and UNIFIED_OCR_PLAN_ENABLED stops being load-bearing.

2. **RESTRUCTURING STEPS**:
   - `helpers/gates.py`: add ~15 lines to `_convert_to_tree` (or wherever gate outcomes are consumed) that does `fn = getattr(RecoveryModule, gate.recovery_fns[i], None); fn(...)` in a loop over `recovery_fns`, with a unit test asserting call-count > 0 for GARBLING/LOW_CONTENT/IMAGE_DOMINANT gates on fixture docs. Net: +15/-0 (closes the dead dispatch — this alone removes the biggest bug class).
   - `picture_plane.py:357-430` (`decide_ocr_strategy`): replace with a single ordered `if/elif` chain of named conditions (already-applied guard, unified-plan image branch, per-picture branch) documented as one function-local truth table; delete the separate mutation points that set/check the guard flag from 3+ call sites and centralize the set to exactly one place (the return of this function). Rough delta: -40/+50 lines but net complexity reduction (one function, one entry, one exit).
   - `client/indexer.py` `force_full_page`: fold into the same decision table as a precondition check rather than a separate late-stage override; delete duplicate logic (~-20 lines).
   - `converters/pictures.py` `_text_layer_has_content`: make it a pure function of `(text, script_context)` with no dependency on whether `clip_text` ran upstream — call it eagerly at the top of the picture-processing entry point so it can never be skipped (~+10/-10).
   - Remove `full_page_already_applied` from `ExtractionState` if the single-pass decision object replaces it; if backward-compat storage is needed, keep it but only as a write-once field set exactly once per document.

3. **HISTORICAL BUG CLASSES PREVENTED**: the UNIFIED_OCR_PLAN_ENABLED-before-guard ordering bug (Zone-2 fix, now permanently fixed by construction); recovery methods (_recover_garble_ocr, _recover_low_content_ocr, _recover_image_dominant_ocr, _recover_vlm_fallback) silently never firing despite passing import-time assertions — the single largest latent-defect class in this zone (4 dead recovery paths); contradictory decisions between OCR sites (fix-one-break-another regressions).

4. **MIGRATION RISK**: Medium-high — wiring dead recovery functions live for the first time will change output on documents that were previously silently under-recovered (garbled/low-content docs that used to reach FAIL/MARGINAL may now recover to PASS, changing verdict distribution across the corpus). Sequence: (a) land the dispatcher wiring behind a feature flag defaulting OFF, run corpus-diagnose/corpus-diff to characterize the verdict deltas, (b) flip default ON only after reviewing deltas against HR5 (no silent low-quality tree persistence), (c) then do the decision-table consolidation in picture_plane.py/indexer.py as a separate, lower-risk pure-refactor PR (behavior-preserving, verified against corpus baselines before/after).

5. **ESTIMATED EFFORT**: 3-4 days (1 day dispatcher wiring + tests, 1 day corpus verification, 1-2 days decision-table consolidation + regression pass).

### Garble Detection NFKC Signal Destruction

1. **CORE SIMPLIFICATION**: Make `ScriptContext` construction go through exactly one factory function (`build_script_context(text)`) that internally calls `_pre_inference_normalize` and always populates `had_presentation_forms`, then delete the ability to construct `ScriptContext` any other way. This turns 'four independent sites replicate the inference' into 'one function, four callers.'

2. **RESTRUCTURING STEPS**:
   - `converters/normalize.py`: no change to `_pre_inference_normalize` itself (138-170) — it's already correct; instead export it plus a thin `build_script_context()` wrapper (~+15 lines) that both computes `had_presentation_forms` and constructs the `ScriptContext`/`RtlDecision` payload together.
   - `script.py`: change `ScriptContext`'s constructor (or add a classmethod `ScriptContext.from_text()`) to be the only sanctioned entry point; grep-driven refactor of the 4 call sites (`_try_image_enrichment`, `_attempt_tesseract_raster_recovery`, indexer.py pre-garble probe, `_text_layer_has_content`) to call `build_script_context()` instead of constructing inline (~-30/+10 across 4 sites — net reduction since each site currently re-implements the fb50/fe70 range check).
   - `helpers/garble.py` `detect_garble` (529-614): delete the compensating fallback at 577-592 (`_had_pf` inference when `_arc>0 and _pf==0`) since it becomes dead code once every ScriptContext is guaranteed to carry the flag correctly — this is the single clearest deletion in the whole audit (~-16 lines, removes a whole compensating-mechanism layer).
   - `_garble_prongs` (339-440): make `presentation_forms` prong graduated (partial score contribution, not unconditional fire) — small change (~+8/-3) with a config threshold, reduces false-positive garble flags.
   - `digit_ratio`/`numeric_junk_short` prongs: lower `garble_digit_floor` default or make it length-adaptive in one place (`config.py`) rather than adding more special-case prongs — prefer tuning the existing floor over adding a fifth prong.
   - `latin_gibberish`: for German T&C corpus, set `expected_script` from a corpus-level config default (`de` → Latin) rather than requiring it be threaded in per-call, closing the structural never-fires gap with a one-line default at the config layer, not new code paths.

3. **HISTORICAL BUG CLASSES PREVENTED**: signal-loss regressions when a new call site is added and forgets to replicate the presentation-forms check (this has already happened 4x); divergence between the compensating fallback's heuristic and the real signal (masking bugs rather than fixing them); false negatives on German T&C docs from `latin_gibberish` never firing.

4. **MIGRATION RISK**: Low-medium. The factory-function refactor is behavior-preserving by construction if `build_script_context()` reproduces `_pre_inference_normalize`'s exact logic (verify with a unit test diffing old vs new `had_presentation_forms` across the existing garble-gate fixture corpus before deleting the compensating fallback). Sequence: (a) add `build_script_context()` alongside existing sites without removing them, (b) migrate call sites one at a time with fixture tests, (c) only delete the compensating fallback in garble.py after all 4 sites are confirmed migrated and corpus diff is clean, (d) tune `latin_gibberish` default last since it's a behavior change, not just a refactor.

5. **ESTIMATED EFFORT**: 2-3 days.

### Table-Unaware Pre-Tree Text Transforms

1. **CORE SIMPLIFICATION**: Apply the exact same guard already proven correct in `headings.py` to `split_oversized_leaf_nodes` — no new abstraction needed, just call the existing `compute_table_spans`/`line_in_table_span` functions from `tree_split.py` inside each of the four fallback split helpers before accepting a split point. This is pure consolidation of an already-correct pattern, not new design.

2. **RESTRUCTURING STEPS**:
   - `helpers/tree_split.py` `split_oversized_leaf_nodes` (398-474): compute `spans = compute_table_spans(lines)` once at the top of the function and thread it into each of the four sub-strategies (~+8 lines for the span computation + threading).
   - `_split_on_atx_headings`, `_split_on_generic_numbered_lines`, `_split_on_paragraph_markers`, `_split_on_blank_line_paragraphs`: add one guard line each — `if line_in_table_span(idx, spans): continue` (or skip that candidate boundary) before accepting a split point (~+4 lines x 4 = +16 lines total).
   - No deletion needed in headings.py — it already does this correctly and is the reference implementation; do not duplicate its logic, just import and call the same two functions already living in tree_split.py (they're already module-local, zero new imports needed).
   - Add a regression fixture: one doc with a wide pipe-table whose rows match `_split_on_generic_numbered_lines`'s numbered-line pattern (this is what actually breaks today) and assert the table stays intact post-split.

3. **HISTORICAL BUG CLASSES PREVENTED**: table rows shattered by numbered-line/paragraph-marker splitting (the RFC-035 D2 landscape chart label shattering is a direct instance); the general asymmetry where a table guard exists in one file but not its sibling in the same file — this closes that specific asymmetry rather than papering over individual symptoms (RFC-028 D1 heading-injection-vs-flat-fallback conflict, RFC-033 D11 TOC over-stripping) which are downstream consequences of the same root unguarded splitter.

4. **MIGRATION RISK**: Low. This is additive-guard-only (no removed behavior, only prevented false-positive split points), so it can only make table-adjacent splitting more conservative, never less correct elsewhere. Sequence: (a) add the guard to one helper (`_split_on_generic_numbered_lines`, the confirmed offender) first with the table fixture, verify no regression on the existing split-boundary test suite, (b) roll the same guard to the remaining three helpers, (c) run full corpus-diagnose to confirm oversized-leaf-node counts don't spike (a table now inside one span may push a leaf node over the size threshold with no valid split point — that's an acceptable, expected outcome per HR5, not a regression).

5. **ESTIMATED EFFORT**: 1 day.

### Verdict Promotion & Hard-Rule-5 Bypass Cascade

1. **CORE SIMPLIFICATION**: Replace the first-match-wins ordered `_matches[0]` promotion pipeline with an explicit, declared priority table (a simple dict/list of (defect, priority) pairs, already partially precedented by `VERDICT_PRIORITY` in queries.py) so ordering is a data declaration, not source-code position — and require the D1 image-enrichment PASS-exception to independently re-verify HR5 (no promotion may create a stored PASS/MARGINAL verdict on a tree that fails `validate_tree()`).

2. **RESTRUCTURING STEPS**:
   - `helpers/verdict.py` `apply_promotions` (405-580): replace the ad-hoc `_matches` list + `_matches[0]` selection (541-576) with a single sorted-by-priority selection over a module-level `PROMOTION_PRIORITY` tuple mirroring the existing `VERDICT_PRIORITY` pattern from `registry/queries.py` — reuse that existing convention instead of inventing a new one (~-15/+20, net neutral line count but removes ordering-is-implicit-in-code-position hazard).
   - Same file, D1 hard-fail exception (519-533): keep the image-enrichment carve-out (functionality preserved) but add an explicit assertion/log at the point of `_apply_clamp(_ie, ...)` that the resulting verdict is re-checked against `validate_tree()` before it's allowed to be a promotion candidate — this is the concrete enforcement of CLAUDE.md hard rule 5 that's currently implicit (~+10 lines).
   - `evaluate_gates` (126-224) zero-content early-return (174-183): before returning the hard `zero_content` FAIL, add one call out to check recovery eligibility for zero-content-eligible gates (if any exist) rather than returning unconditionally — if no recovery path applies to zero-content today, document that explicitly in a comment rather than leaving it silently bypassed (~+5 lines, or 0 lines + comment if genuinely no recovery applies, in which case this sub-item is a no-op — confirm via `trace_path` before touching).
   - `config.py` PASS_MAX_LEAF_RATIO: do not widen further; instead add a corpus regression test that fails CI if the threshold is changed without a corresponding fixture-doc justification (~+20 lines of test, 0 production code change) — this converts 'threshold changes mask defects' from a recurring bug source into a gated, reviewed decision.

3. **HISTORICAL BUG CLASSES PREVENTED**: HR5 violations where threshold-widening silently downgraded zero-content Arabic PDFs from FAIL to MARGINAL; promotion winner flipping when helper functions are reordered in source (a refactor-time landmine); zero-content docs being FAILed without ever being considered for recovery.

4. **MIGRATION RISK**: Medium — changing `_matches[0]` to explicit priority selection must produce byte-identical winner selection to today's source-order behavior for the current helper set (verify with a table asserting `PROMOTION_PRIORITY` order == current source order before any behavior change is intended). Sequence: (a) land priority-table refactor as pure behavior-preserving (assert identical winners across corpus before/after), (b) separately add the CI threshold-change gate (zero production risk), (c) investigate the zero-content/recovery gap last since it's the only item here that could be a genuine behavior change (confirm with trace_path whether any recovery_fn targets zero-content before writing code — if none do, this is a documentation-only fix, not a code fix).

5. **ESTIMATED EFFORT**: 2 days.

### Verdict Persistence Dual-Writer & Hysteresis Fragility

1. **CORE SIMPLIFICATION**: Make `save_doc_meta` use the same CAS-by-priority discipline the Postgres `_UPSERT_SQL` already implements, instead of unconditionally merging — this eliminates the transient-disagreement window without inventing a new consistency model, just extending the existing `VERDICT_PRIORITY` dict to the MinIO-meta writer. Make `_upsert_registry_row`'s False-on-failure return type impossible to silently ignore by narrowing its signature to a result enum that reconcile must exhaustively match on.

2. **RESTRUCTURING STEPS**:
   - `storage/verdict.py` `save_doc_meta` (78-198): import `VERDICT_PRIORITY` from `registry/queries.py` (already exists, no new lookup table); before merging `_MERGE_FIELDS`, compare `VERDICT_PRIORITY[new_verdict] >= VERDICT_PRIORITY[existing_verdict]` and skip the verdict/verdict_reason/max_leaf_ratio fields (but still merge other non-verdict fields) if the incoming verdict has lower priority (~+15 lines). This is the direct fix for the dual-writer disagreement — reuse, not new abstraction.
   - Same file: add a `_confirm_write_visible` call after the meta write, mirroring what `save_doc`/`save_flat_doc` already do (documented asymmetry removed) (~+8 lines, calling the existing `minio_ops.py` helper — zero new code in minio_ops.py).
   - `worker/registry_mirror.py` `_upsert_registry_row` (136-315): keep returning bool for compat, but rename the degraded-path returns to route through a single `_RegistryWriteResult` enum (SUCCESS/DISABLED/POOL_NOT_READY/WRITE_FAILED) internally, with the public bool being `result == SUCCESS` — reconcile's caller then pattern-matches on the enum instead of a bare bool, making 'silently ignoring False' a type error rather than a possible oversight (~+20/-10). This directly hardens against the RFC-042-D3-class bug (reconcile deleting retry keys on unconditional True/False) recurring elsewhere.
   - Hysteresis / `find_prior_verdict`: no structural code change proposed — the actual defect is operational (standard corpus reingestion wipes `processed/*.meta.json`, destroying the ledger). Document in `ARCHITECTURE.md` that reingestion must not wipe meta.json without an explicit ledger-preserving flag, and add a corpus-ingest guard (in the `corpus-ingest` skill/script, not core code) that refuses to blanket-delete `processed/*.meta.json` unless a `--reset-hysteresis` flag is passed (~+10 lines in the ingest script, not core).

3. **HISTORICAL BUG CLASSES PREVENTED**: dual-writer disagreement between MinIO meta and Postgres registry (save_doc_meta unconditional merge vs. Postgres CAS); reconcile silently discarding retry keys after a failed registry write (the exact RFC-042 D3 class bug, now type-enforced against recurrence); verdict flapping (PASS→MARGINAL) on identical trees caused by ledger loss during reingestion.

4. **MIGRATION RISK**: Medium — the CAS-by-priority change in `save_doc_meta` is a behavior change for any in-flight scenario where a lower-priority verdict was previously allowed to overwrite a higher one (this was arguably always a bug, so tightening it should only fix incorrect states, but verify via corpus-diff that no currently-PASS doc regresses due to a stale lower-priority write winning historically). Sequence: (a) land `_confirm_write_visible` addition first (pure safety net, zero behavior change to happy path), (b) land CAS-by-priority gating behind a flag, run corpus-diagnose to compare verdict outcomes before/after, (c) land the registry_mirror enum hardening last since it's the most mechanical/lowest-risk change but touches the most call sites, (d) the reingestion guard is fully independent and can land anytime.

5. **ESTIMATED EFFORT**: 2-3 days.

---

**End of Audit Report**