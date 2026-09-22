---
id: RFC-047
title: Gate-Layer Correctness
type: rfc
status: draft
date: 2026-09-22
plan-impact: no
tags: [rfc, gate-correctness, verdict, garble-detection, density-gate, flat-routing]
aliases: [RFC-047, Gate-Layer Correctness]
governs: ["[[design-rfc047-gate-layer-correctness]]", "[[tasks-rfc047-gate-layer-correctness]]"]
supersedes: []
---

## Context

RFC-046's post-wave-7 panel review surfaced four bugs in the verdict and gate layer that are structurally distinct from the six failure clusters (C1--C6) RFC-046 fixed. RFC-046 deliberately did not fix them: each sits in the gate/verdict computation layer rather than the OCR attribution or cluster-remediation layer, and mixing the two scopes would have made corpus-delta attribution impossible.

The four bugs share a common shape: a gate or detector produces a result, and the layer that consumes it either conflates inputs, ignores the result, inherits a stale result from a different evaluation, or over-matches on a syntactically broad pattern. In each case the stored verdict is wrong -- either a false condemnation or a silently persisted low-quality artifact -- and the defect is in the plumbing, not in a threshold or an engine.

**How they were found.** The panel review examined the attributed corpus baseline ([[rfc046-wave1-attributed-baseline]]) and traced every remaining anomalous verdict to the line responsible. Three of the four bugs had been observed before -- the `_MIXED_SCRIPT_RE` asymmetry is noted in memory ("Garble PF detector asymmetry"), the flat-path defect inheritance is noted in memory ("Flat verdict uses tree signals"), and the `ocr_text`/`summary` conflation was flagged as the reason D8 activation was held. The fourth (HR5 post-enrichment garble fall-through) was found by code inspection during the panel.

**Why a separate RFC.** RFC-046 scoped P0 (baseline truth) and P0.5 (cluster remediation) from [[plan-rfc046-surya-quality-fallback]]. Its twelve deliverables (D1--D12) are complete or in flight. Adding four more bugs to it would (a) reopen a scope that has a corpus gate at its exit, (b) make the Wave 7 corpus delta unattributable (movements from RFC-046's fixes and from these fixes would be merged), and (c) delay the engine-tier decision that RFC-046's successor was meant to enable. A clean boundary serves all three concerns.

**Note on numbering.** RFC-046's Context section names RFC-047 as the OCR-engine tier successor. That numbering is superseded: the gate-layer bugs are more urgent than the engine question (they cause false verdicts on the current engine), so RFC-047 is this correctness RFC. The OCR-engine tier, if written, becomes RFC-048, scoped against the residue of both RFC-046 and RFC-047.

### Relationship to Prior RFCs

- **[[RFC-046]]** (parent investigation): surfaced all four bugs during its panel review. D8 activation was explicitly held pending the `ocr_text`/`summary` split this RFC delivers (D5). The flat-path defect re-derivation (D4) was identified as panel decision #5 but deferred.
- **[[RFC-045]]** (Arabic garble chain): the `_MIXED_SCRIPT_RE` regex (D1) is part of the garble detection subsystem RFC-045 repaired. RFC-045's fix targeted the NFKC/presentation-forms path; this RFC targets the sparse-mojibake prong's ASCII character class.
- **[[RFC-044]]** (authority): the `decide_route` mapping and `decide_ocr_strategy` authority model are upstream of the flat-path defect (D4) but are not modified here.
- **[[RFC-029]]** (density floor): the density gate at `gates.py:345` consumes `flat_text_corrected`, whose semantics D5 corrects. The floor value `_RFC029_MIN_SCANNED_DENSITY_FLOOR` is not changed.

### Relationship to RFC-046 Open Tasks

- **Task 4.4** (garble-check enrichment-mutated blocks): subsumed by D3. RFC-046 task 4.4 is closed by this RFC's D3 delivery.
- **Task 5.3** (reconcile the two arbitrators): NOT addressed here. Deferred to the engine-tier successor RFC, where `ENGINE_RELIABILITY_ORDER` and multi-engine arbitration belong.

## Goals

1. Fix the `_MIXED_SCRIPT_RE` false-positive on legitimate Arabic reference numbers and parenthesized bullet markers, with attributed corpus measurement showing the prong no longer fires on clean documents.
2. Wire a consequence onto the post-enrichment garble check so that garbled OCR content from image enrichment is never silently persisted -- closing the Hard Rule #5 surface -- with a ratio threshold that prevents a single garbled chart caption from condemning an entire document.
3. Make the flat-path verdict compute its defects from flat signals, not from inherited tree defects, so that a flat document is never condemned by a gate that fires only on the tree.
4. Split the `include_enrichment` flag in `_node_text_parts` into independent `include_ocr_text` and `include_summary` flags, so the density-corrected numerator (`flat_text_corrected`) counts only real OCR text and not LLM-generated summaries, unblocking D8 activation from RFC-046.
5. Add a script-aware Arabic density floor so that Arabic documents with expected lower OCR yield are not falsely condemned by a Latin-calibrated density threshold.
6. Add a Surya OCR fallback for Arabic documents that fail the density gate even with the lowered floor, recovering genuinely sparse extractions where Surya yields more text than Tesseract.

## Non-Goals

1. ~~**No OCR engine changes.**~~ **Revised 2026-09-22:** D8 adds Surya as an Arabic-specific density-recovery fallback, scoped narrowly to documents that fail the `suspect_density` gate on Arabic-dominant PDFs. No Paddle, VL, or general engine-selection changes. General multi-engine arbitration remains deferred to a successor RFC.
2. ~~**No threshold changes.**~~ **Revised 2026-09-22:** D7 lowers the general density floor from 1500 to 1200 and adds a script-aware Arabic density floor (`RFC029_MIN_SCANNED_DENSITY_FLOOR_ARABIC`, default 800). All other thresholds (`PASS_MAX_LEAF_RATIO`, `hard_fail_max_leaf_ratio`, `VerdictThresholds`) remain untouched.
3. **No RFC-044 Phase B.** Authority consolidation is out of scope.
4. **No new recovery methods** beyond D8's density-recovery fallback. The HR5 consequence wiring (D3) rejects or strips garbled blocks; D8 re-OCRs with Surya only when the density gate fires on an Arabic-dominant document.
5. **No D6 activation.** D5 unblocks the activation decision; taking it is a separate deliverable (D6), gated on corpus measurement. D6 remains DEFERRED — the shadow metric `_would_fire_corrected` provides observability.
6. **C6 (garble-primary masks flat lifeboat).** `decide_route` maps `GARBLING → RETRY_OCR → Route.TREE` unconditionally (`types.py:371-372`). This is a routing-authority defect belonging to [[RFC-044]] Phase B, not a gate-correctness issue. Deferred.
7. **`ENGINE_RELIABILITY_ORDER` cleanup.** `arbitrate.py:20-24` lists `surya`/`paddleocr`/`paddleocr-vl` — names that appear nowhere else in `src/`. This is dead multi-engine framework, not a gate bug. Belongs in the engine-tier successor RFC.

## Deliverables

### D1: `_MIXED_SCRIPT_RE` Repair

**File:** `src/pageindex_mcp/helpers/garble.py`
**Lines:** 752--755 (regex definition), 542--545 (usage in `_garble_prongs`)

**Defect.** The regex uses `[\x21-\x7E]` (all printable ASCII) as the bridging character class between Arabic codepoints:

```python
_MIXED_SCRIPT_RE = re.compile(
    r"[؀-ۿ][\x21-\x7E]{1,8}[؀-ۿ]"
    r"|[\x21-\x7E]{1,8}[؀-ۿ][\x21-\x7E]{1,8}"
)
```

This matches digits 0--9 (`\x30`--`\x39`), parentheses, periods, and commas -- all of which appear legitimately adjacent to Arabic text in insurance and legal documents. Parenthesized Arabic letter markers `(أ)`, `(ب)` and article references `المادة15` are standard formatting, not encoding errors. The `sparse_mojibake` prong fires at a 2% word-ratio threshold (line 544), the lowest of any of the 12 prongs, and since `is_garbled = bool(prongs)` (line 741), a single false prong condemns the document.

**Fix.** Add a lookahead assertion requiring at least one ASCII letter `[A-Za-z]` within the bridging ASCII run for the first alternation. Split the second alternation into two branches requiring a letter on EITHER side of the Arabic codepoint (OR, not AND), avoiding false negatives for mojibake with a letter on only one side. True mojibake from broken Arabic encoding produces Latin letter substitutions (`كtابcجديد`); legitimate adjacency involves only digits and punctuation. The fixed regex:

```python
_MIXED_SCRIPT_RE = re.compile(
    r"[؀-ۿ](?=[\x21-\x7E]{0,7}[A-Za-z])[\x21-\x7E]{1,8}[؀-ۿ]"
    r"|[A-Za-z][\x21-\x7E]{0,7}[؀-ۿ][\x21-\x7E]{1,8}"
    r"|[\x21-\x7E]{1,8}[؀-ۿ][\x21-\x7E]{0,7}[A-Za-z]"
)
```

**Verified against existing tests:** all six assertions in `tests/test_garble.py::TestSparseMojibake`, `tests/test_rfc_reorder.py::TestSparseMojibakeDetection`, and `tests/test_rfc_quality.py:315` pass unchanged because their fixtures contain ASCII letters (`x`, `z`, `q`, `k`, `X`, `Y`, `Z`) within the bridging runs.

**D1 acceptance criterion (added 2026-09-22).** Mechanically verify the proposed 3-alternation regex against each existing mojibake test fixture. Confirm match counts still exceed the 0.02 word-ratio threshold for the `sparse_mojibake` prong. Note: `_sparse_text` uses original (un-normalized) text, not NFKC-normalized blob — the lookahead fix must account for presentation-form Arabic codepoints in `original_text`.

**New tests required:**
- Parenthesized Arabic letters `(أ)` must not fire `sparse_mojibake`
- Digit-only bridging `رقم597` must not match
- A realistic Arabic insurance document with section markers must remain clean

### D2: `_garble_check_flat_blocks` Ratio Threshold

**File:** `src/pageindex_mcp/helpers/garble.py`
**Lines:** ~974 (`if not garbled_count: return None`)

**Defect.** The function returns a `GarbleReport` if ANY single block is garbled (`garbled_count >= 1`). While it computes `garble_ratio` (line 989), the ratio is stored in report attrs only -- the return/no-return decision is binary on count. A single garbled chart caption among many clean image blocks condemns the entire enrichment.

**Fix.** Change the threshold from `if not garbled_count: return None` to a ratio-aware check: `if garble_ratio < _GARBLE_BLOCK_RATIO_THRESHOLD: return None`. Add a module-level constant `_GARBLE_BLOCK_RATIO_THRESHOLD = 0.10` at `garble.py:~764`, following the pattern of `_RFC029_DEEP_TREE_DEPTH_THRESHOLD`. Default: **0.10** (not 0.3 — see rationale below). Promote to `GarbleConfig` only when a second consumer or deployment-specific override appears (YAGNI). This prevents a single garbled decorative element from triggering a document-level rejection.

**Threshold rationale (corrected 2026-09-22).** The original proposal of 0.3 was refuted by multi-agent review: `test_single_garbled_block_not_diluted` (`tests/test_garble.py:1134`) creates 5 blocks with 1 garbled (ratio 0.2) and asserts condemnation. A threshold of 0.3 would cause ratio 0.2 < 0.3 → return None, violating the RFC-026/RFC-027 dilution immunity invariant. The value 0.10 clears وارد رقم 597 at 0.017 (well below), preserves dilution immunity at 0.2 (well above), and aligns with the existing `garble_node_ratio_threshold = 0.10` pattern.

**Tri-caller note (corrected 2026-09-22).** `_garble_check_flat_blocks` is called from **three** sites, not two: the main flat-blocks garble gate (`indexer.py:1409`), the **VLM-fallback recovery check (`indexer.py:1451`)**, and the post-enrichment gate (`indexer.py:1532`). All three pass the **identical** config expression `_image_garble_cfg if _image_garble_cfg is not None else _garble_config` — the claimed per-caller `GarbleConfig` differentiation does not exist without a code change. Per-caller config is deferred to a future investigation; for now, the uniform module-level constant applies to all three sites.

**Dependency:** Must land alongside or after D1. Without the `_MIXED_SCRIPT_RE` repair, the enrichment garble check false-positives on legitimate Arabic reference patterns, and raising the ratio threshold on a detector that over-matches would mask real garble.

**Tri-caller impact (corrected 2026-09-22):** This threshold change affects all **THREE** callers of `_garble_check_flat_blocks` — the main garble gate (`indexer.py:1409`), the VLM-fallback recovery check (`indexer.py:1451`), and the post-enrichment gate (`indexer.py:1532`). All three become ratio-aware. The main gate currently rejects any document with any garbled block; with a ratio threshold of 0.10, documents with fewer than 10% garbled blocks on the main path would start passing. This is acceptable given the dilution immunity guard at 0.2 holds.

**Post-gate-FAIL correction — character-mass ratio (2026-09-22).** The Wave 1 gate FAIL demonstrated that using **block count** as the denominator disables single-block garble detection for every document above 10 blocks (1/N < 0.10 when N > 10). The entire real corpus has documents at 198–297 blocks, so the gate was effectively off. The fix replaces the block-count ratio with a **character-mass ratio**: `garbled_chars / total_chars`, where `garbled_chars` is the sum of `len(block_text(b))` for all garbled blocks and `total_chars` is the sum for all checked blocks. Character counts are already available from `block_text()` — no new data source needed. The `_GARBLE_BLOCK_RATIO_THRESHOLD` constant (0.10) is repurposed to apply to character mass rather than block count. This catches a single garbled block that holds 60% of a document's characters while still tolerating a tiny garbled caption among hundreds of clean blocks.

**VLM site alignment (2026-09-22).** The VLM-fallback site (`indexer.py:1451`) uses the same character-mass approach. VLM output should be clean — if the vision model can't produce clean text, the recovery should be marked as failed. The same 0.10 character-mass threshold applies uniformly across all three sites for now.

**flat_meta garble persistence (2026-09-22).** Sub-threshold garble metrics (ratio, fired prongs) MUST be persisted into `flat_meta` regardless of whether the threshold was crossed. Currently, below-threshold garble prongs are dropped from `flat_meta` entirely — the document is persisted with no trace that garble was ever detected. This violates Hard Rule #5. The fix: always write `flat_meta['garble_ratio']` and `flat_meta['garble_prongs']` from the flat-blocks check, even when the verdict is "below threshold / PASS". This ensures downstream consumers and future threshold changes don't require re-processing.

### D3: HR5 Post-Enrichment Garble Consequence Wiring

**File:** `src/pageindex_mcp/client/indexer.py`
**Lines:** 1531--1561 (post-enrichment garble check), 2128--2130 (`flat_garble_unrecovered_reject` guard)

**Defect.** After image blocks are enriched with OCR text (line 1519, `_apply_picture_enrichment`), the code runs `_garble_check_flat_blocks` on the enriched blocks (line 1532). When garble is detected (`_enrich_garble` is truthy, line 1545), a decision event is logged -- and then execution falls through. The garbled OCR content is persisted into the flat structure (lines 1579--1584) and written to MinIO. The `_enrich_garble` result is write-only: logged but never acted on.

The existing reject guard at lines 2128--2130 checks `state.flat_garble_unrecovered`, which is set only by the MAIN flat-blocks garble gate (line 1415). The post-enrichment garble result is never written back into any state field that reaches the reject guard. This violates CLAUDE.md Hard Rule #5: "Never silently persist a low-quality tree."

**Fix.** Surgical field-clear (option B, refined 2026-09-22): when `_enrich_garble` is truthy and the garble ratio exceeds the D2 threshold, clear the `ocr_text` field on each garbled image block (`block['ocr_text'] = ''`) rather than removing blocks from the list. This preserves image metadata (`figure_path`, `page`, `bbox`), avoids list mutation and insertion-point sensitivity, and the empty `ocr_text` yields empty string from `block_text()`, so the block contributes zero text to downstream consumers. Same effect as list removal, simpler implementation.

**Insertion point:** The field-clear loop executes after line ~1554 (decision log for `post_enrichment_garble_check`) and before line ~1580 (`flat_structure` construction). All four downstream consumers (`flat_structure`, `flat_char_count`, `row_records`, `flat_meta['blocks']`) see only clean content.

**Per-block identification mechanism:** Replace the `_garble_check_flat_blocks` call at the post-enrichment site with an inline per-block loop: test each enriched image block with `detect_garble` individually, count garbled, compute ratio, and if `ratio >= _GARBLE_BLOCK_RATIO_THRESHOLD` then clear `ocr_text` on the garbled blocks. This produces per-block identification naturally without modifying `_garble_check_flat_blocks`'s return type.

**Edge case — all blocks garbled:** When ALL enriched image blocks are garbled (ratio = 1.0), all `ocr_text` fields are cleared. The document proceeds with zero image-derived text but retains its image metadata and non-image content. This is correct: the main flat-blocks garble gate (`indexer.py:1409`) provides a second check on overall document quality.

Extend the existing `post_enrichment_garble_check` decision event in `decision_points.py` (`src/pageindex_mcp/obs/decision_points.py`) with `stripped_count`/`retained_count`/`garble_ratio` attrs, rather than registering a separate `post_enrichment_garble_strip` event. This avoids a new AST guard entry and reuses the existing event.

**Dependencies:** MUST land after D1 (regex repair) and D2 (ratio threshold). Without D1, the regex false-positives on legitimate Arabic patterns. Without D2, a single garbled chart caption condemns all enriched blocks.

**Sequencing rationale.** The three-part dependency chain (D1 -> D2 -> D3) exists because each layer narrows its predecessor. D1 narrows what the regex matches. D2 narrows when matches become a garble verdict. D3 wires the verdict to a consequence. Wiring the consequence before the narrowing would produce document-level false rejections -- a single garbled decorative element in an otherwise clean document would discard all enriched image content.

### D4: Flat-Path Defect Re-Derivation

**File:** `src/pageindex_mcp/helpers/verdict.py` lines 123--277 (`evaluate_gates`); call site at `src/pageindex_mcp/client/indexer.py` lines 1593--1599

**Defect.** When a document fails as a tree and is retried as flat blocks, the call site (indexer.py:1593) passes `state.gate_result` (the TREE's `TreeGateResult`) to `compute_verdict` alongside `flat_signals` derived from the flat blocks. Inside `evaluate_gates`:

```python
if isinstance(validate_result, TreeGateResult):
    defect = validate_result.defect
    ...
    _all_defects = validate_result.all_defects
...
if flat_signals is not None:
    sig = flat_signals          # <-- overrides signals
    # but defect and _all_defects remain from the tree
```

The `sig` (signals) is overridden by `flat_signals`, but `defect` and `_all_defects` remain frozen from the tree evaluation. At line 233, the masked hard-fail check uses `_all_defects & HARD_FAIL_DEFECTS`:

```python
_masked = _all_defects & HARD_FAIL_DEFECTS
if _masked:
    ...
    return GateOutcome(
        ...
        hard_fail_verdict=("FAIL", ...),
    )
```

`SUSPECT_DENSITY` from the tree's `all_defects` fires the hard-fail path and produces a FAIL verdict, even though the flat signals have sufficient text to clear the density gate. The flat signals override at line 167 fixes only the signal object -- it does NOT re-derive defects from the flat structure.

**Concrete example.** The uae_numbers portrait document has `chars_per_page=1427` on the tree (fires `SUSPECT_DENSITY` at the scanned-density floor), but the flat path has `flat_text_len=2151`. The flat verdict inherits the tree's condemnation.

**Fix.** Approach (A): at the call site (indexer.py:1593), pass `None` instead of `state.gate_result` as `validate_result` when computing the flat verdict. This makes `evaluate_gates` enter the `validate_result is None` branch (verdict.py:187--200), where `defect = TreeDefect.OK` and `_all_defects = frozenset()`. All hard-fail defects are absent, and the flat signals drive the verdict. Add a decision-log entry recording that the tree's `gate_result` was discarded for the flat verdict.

```python
# Before (indexer.py:1596)
state.gate_result,

# After
None,  # flat path: derive defects from flat signals, not from tree
```

**Verdict movement.** Documents that currently FAIL on the flat path due to inherited tree defects will receive the verdict their flat signals produce. For the uae_numbers portrait: FAIL -> clears density gate -> proceeds to Phase 2 promotions -> expected PASS or MARGINAL.

**Mandatory red-green test (added 2026-09-22).** D4 MUST include a test that demonstrates the bug before the fix: construct a `TreeGateResult` with `defect=SUSPECT_DENSITY` and `all_defects` containing `SUSPECT_DENSITY`, pass it alongside `flat_signals` with sufficient text (`flat_text_len=2151`), and assert the outcome hard-fails with `SUSPECT_DENSITY`. This test MUST FAIL after the fix (passing `None` removes the hard-fail). The existing `TestFlatSignalsOverrideTreeSignals` (`test_d6_flat_verdicts.py:28`) passes `TreeGateResult(defect=TreeDefect.OK)` — it tests signal override with a clean tree, NOT defect leakage.

**Reorder-inference branch safety.** Passing `None` as `validate_result` activates the reorder-inference branch (`verdict.py:192-200`), which infers `REORDERED` from `sig.is_reordered`. Flat structures currently never carry `start_index`/`line_num`, so `is_reordered` is always `False` — but this safety is an implicit contract. D4 must include a test confirming flat structures produce `is_reordered=False`.

**Metadata provenance fix.** After D4 passes `None`, `flat_meta['garble_prongs']` at `indexer.py:1650-1653` still reads from `state.gate_result.signals.garble_prongs` (tree). D4 should source this from `_flat_sig` (already computed at line ~1590) to maintain provenance consistency between the flat verdict and its metadata.

### D5: `ocr_text`/`summary` Field Split

**File:** `src/pageindex_mcp/helpers/tree_validation.py` lines 110--114 (`_node_text_parts`), line 298 (`flat_text_corrected` construction), line 281 (`TreeSignals.flat_text_corrected` field); consumer at `gates.py:345`

**Defect.** `_node_text_parts` accepts a single boolean `include_enrichment`. When True, it appends BOTH `summary` (LLM-generated paraphrase) and `ocr_text` (real OCR output from image blocks) into the parts list:

```python
if include_enrichment:
    for field in ("summary", "ocr_text"):
        value = str(n.get(field, ""))
        if value and value not in parts:
            parts.append(value)
```

The `summary` field is a synthetic expansion of existing content -- it does not represent text that was "in the document but uncounted." Including it in `flat_text_corrected` inflates the density numerator by 42% on the uae_numbers portrait document. This inflation is why the panel held D8 activation (switching the density gate to fire on the corrected value): the corrected value is poisoned by summary inflation.

**Fix.** Split the single `include_enrichment` boolean into two independent flags:

```python
def _node_text_parts(
    n: dict,
    *,
    include_ocr_text: bool = False,
    include_summary: bool = False,
) -> list[str]:
```

Propagate the same two flags through `_flatten_tree_text`. In `TreeSignals.from_tree` (line 298), build `flat_text_corrected` with `include_ocr_text=True, include_summary=False`. Remove the old `include_enrichment` parameter (no external callers use it -- confirmed by grep).

**Downstream impact.** The density gate at `gates.py:345` continues to use `flat_text_corrected`, which now contains only OCR text augmentation. D8 activation (switching the gate to fire on the corrected value) becomes safe.

**Facade guard.** `tests/test_facade_surface_guard.py` exports both `_node_text_parts` (line 319) and `_flatten_tree_text` (line 306). Both functions' signatures change in lockstep — the guard must be updated for BOTH, not only `_node_text_parts`.

**Dedup behavior note.** `_node_text_parts` lines 113-114 silently drop `ocr_text`/`summary` values that duplicate body text. This behavior is preserved after the split and prevents inflation from duplicated content. Implementation must not change this dedup check.

### D6: D8 Activation Decision

Taken AFTER D5 lands and a corpus measurement shows the split numerator. This is a decision gate, not a code deliverable: the measurement determines whether the density gate switches to firing on the corrected (OCR-only) value or remains on the uncorrected value.

**Acceptance criteria:**
1. D5 SHALL have landed and a corpus run SHALL have been taken with the split numerator.
2. A per-document table SHALL show, for every document, the old `chars_per_page_corrected` (summary + ocr_text) and the new value (ocr_text only), and which documents would change verdict.
3. The decision SHALL be recorded with a dated entry and the measurement that drove it.

### D7: Script-Aware Arabic Density Floor

**File:** `src/pageindex_mcp/helpers/gates.py` (density gate), `src/pageindex_mcp/config.py` (config field)

**Motivation.** The `suspect_density` gate at `gates.py:325` uses a single floor (`_RFC029_MIN_SCANNED_DENSITY_FLOOR`, default 1200 chars/page) for all documents regardless of script. Arabic OCR consistently under-extracts compared to Latin-script documents -- the RFC-046 eval report shows Tesseract yields fewer characters on Arabic PDFs, and Surya (the strongest Arabic engine) still produces fewer chars/page than Latin equivalents. A script-aware floor acknowledges this reality.

**Evidence from corpus.** Document #24 (اتفاقية مستوى الخدمة) has 1211.6 chars/page and is FAIL. This is a 20-page Arabic scanned PDF where Docling/Tesseract extracts sparse but real text. A Latin document at the same density would be genuinely suspect; an Arabic document at this density is within expected OCR yield.

**Design.**
1. Add config field `rfc029_min_scanned_density_floor_arabic: float` sourced from env var `RFC029_MIN_SCANNED_DENSITY_FLOOR_ARABIC`, default **800**.
2. In `_gate_suspect_density` (`gates.py:325`), when `expected_script.dominant_script == "Arab"`, use the Arabic-specific floor instead of the general floor.
3. Log both floors in the `suspect_density_gate` decision event attrs (`floor_used`, `is_arabic`).
4. The gate already receives `expected_script: ScriptContext` -- no new parameter threading needed.

**Threshold rationale.** 800 chars/page is 2/3 of the general 1200 floor (lowered from 1500 in this RFC). This accommodates Arabic OCR's lower yield while still catching genuinely empty or garbled extractions. Document #15 (القرار التنظيمي) at 153 chars/page is far below even the Arabic floor -- it is a genuine extraction failure that D8 (Surya fallback) addresses.

### D8: Surya OCR Fallback for Arabic Density Failures

**File:** `src/pageindex_mcp/client/indexer.py` (density-fail recovery path), `src/pageindex_mcp/config.py` (config fields)

**Motivation.** After D7 lowers the Arabic density floor, document #15 (القرار التنظيمي, 153 chars/page) remains FAIL -- the extraction is genuinely too sparse. The RFC-046 multi-engine eval (`agents/spikes/ocr_eval_rfc046/eval_report.md`) shows Surya yields more characters on this document (3759 vs 3731 from Tesseract) with 95.57% confidence. Across all 10 Arabic documents, Surya wins 7/10 by char yield with 94--98% confidence.

**Infrastructure.** The Surya OCR service already exists:
- Docker service: `services/surya-ocr-service/` with `Dockerfile`, `app.py`, `pyproject.toml`
- `docker-compose.yml`: `surya-ocr-service` at port 8207
- `arbitrate.py:29`: `"surya"` is already in `ENGINE_RELIABILITY_ORDER`
- GPL-3.0 licensed (same copyleft family as pymupdf4llm's AGPL-3.0 -- same legal clearance concern, per Hard Rule #4)

**Design.**
1. Add config fields: `SURYA_FALLBACK_ENABLED` (default `false`), `SURYA_SERVICE_URL` (default `http://localhost:8207`).
2. In the density gate recovery path: when `suspect_density` fires AND `expected_script.dominant_script == "Arab"` AND `SURYA_FALLBACK_ENABLED=true`, re-OCR the document pages via the Surya service.
3. Recompute `chars_per_page` from Surya output. If the new density clears the floor, use Surya's text; otherwise, keep the original (Tesseract) result and let the FAIL stand.
4. Log the fallback attempt and outcome via a new `surya_density_fallback` decision event.
5. The `arbitrate.py` multi-engine framework is NOT used -- this is a targeted density-recovery path, not general engine arbitration.

**Expected outcomes:**
- #15 القرار التنظيمي (153 cpp): Surya yields 3759 chars → ~188 cpp on 20 pages, or higher if Surya extracts more per-page. If Surya clears the Arabic floor (800), the document recovers to PASS/MARGINAL.
- #24 اتفاقية مستوى الخدمة (1211.6 cpp): Already recovered by D7's Arabic floor (1211.6 > 800). Surya fallback does not fire.

**Licensing note.** Surya is GPL-3.0 (Endless Labs, Inc.). Same copyleft concern as pymupdf4llm/PyMuPDF (AGPL-3.0) per Hard Rule #4. The Surya service runs as a separate container behind an HTTP API, which may provide process-level isolation from the main MIT-licensed codebase. Legal clearance is the same decision surface as the existing AGPL dependency.

### D9: Full Corpus Re-Run and Engine Decision

A full corpus run after D1--D8 have landed, producing an attributed per-document delta table. This is the final gate for RFC-047:

1. Every verdict movement SHALL be attributed to a named deliverable (D1--D8).
2. Movements SHALL be reported in both directions -- improvements and regressions.
3. An unexplained movement SHALL block acceptance pending investigation.
4. The residue (documents still failing after all gate-layer fixes + Arabic recovery) determines whether an engine-tier successor RFC is still needed.

## Waves

### Wave 1: D1 + D2 (Detector Fixes)

**Scope:** `_MIXED_SCRIPT_RE` regex repair and `_garble_check_flat_blocks` ratio threshold.

**Rationale:** These are detector-level fixes with no verdict movement until wired. D1 narrows what the regex matches; D2 narrows when matches become a garble verdict. Both must land before D3 wires a consequence.

**Exit gate:** All existing garble tests pass. New tests for parenthesized Arabic markers and digit-only bridging pass. The character-mass ratio correctly detects garble at N=198 and N=297 blocks. Sub-threshold garble metrics are persisted in `flat_meta`. VLM site uses the same character-mass approach.

### Wave 2: D3 (HR5 Consequence Wiring)

**Scope:** Connect the post-enrichment garble check to a surgical strip of garbled blocks.

**Dependency:** Wave 1 (D1 + D2). The consequence must not be wired until the detector is accurate and ratio-aware.

**Exit gate:** A document with garbled image enrichment has the garbled blocks stripped before persistence. A document with one garbled chart caption among 10+ clean image blocks is NOT rejected. Hard Rule #5 surface is closed.

### Wave 3: D4 (Flat-Path Defect Re-Derivation)

**Scope:** Pass `None` as `validate_result` on the flat path so defects are not inherited from the tree.

**Independence:** This wave has no dependency on Waves 1--2. It touches `evaluate_gates`' signal-selection contract and must land alone so its corpus delta is attributable.

**Exit gate:** A flat document whose tree had `SUSPECT_DENSITY` but whose flat blocks have sufficient text no longer receives a hard-fail FAIL. The uae_numbers portrait document clears the density gate on the flat path.

### Wave 4: D5 + D6 (Field Split + Density Activation Decision)

**Scope:** Split `include_enrichment` into `include_ocr_text` and `include_summary`. Take the D8 activation decision on the measured split numerator.

**Exit gate:** `flat_text_corrected` contains only OCR text augmentation. The panel's "Hold; split fields first" precondition is discharged. D6 records the activation decision with its measurement.

### Wave 5: D7 + D8 (Arabic Density Recovery)

**Scope:** Script-aware Arabic density floor (D7) and Surya OCR fallback for Arabic density failures (D8).

**Dependency:** Waves 1--4 (D1--D6). The Arabic recovery path builds on the corrected gate layer.

**Exit gate:** Arabic density floor is script-aware. Surya fallback fires on Arabic density-failed documents and recovers those where Surya yields sufficient text. Tests cover both the floor switch and the fallback path. No regressions in existing test suite.

### Wave 6: D9 (Final Corpus Re-Run)

**Scope:** Full corpus run with all D1--D8 applied, attributed per-document delta table, final engine-tier decision.

**Exit gate:** Every movement attributed to D1--D8. Residue documented. Engine-tier successor RFC scoped against the residue, or recorded as unnecessary. RFC-047 is marked complete.

## Test Strategy

- **D1:** Existing `TestSparseMojibake`, `TestSparseMojibakeDetection`, and `test_rfc_quality.py:315` assertions pass unchanged. New tests: parenthesized Arabic letters `(أ)` must not fire `sparse_mojibake`; digit-only bridging `رقم597` must not match; a realistic Arabic insurance document with section markers must remain clean.
- **D2:** Unit test: `_garble_check_flat_blocks` with a mix of clean and garbled blocks returns `None` when character-mass ratio is below threshold and a `GarbleReport` when above. Critical regression: a single garbled block among 198 or 297 clean blocks with high character mass IS detected (the block-count denominator bug). Regression test: one garbled chart caption (low char mass) among 10+ clean blocks does NOT trigger rejection. Persistence test: sub-threshold garble metrics (ratio, prongs) are always written to `flat_meta`.
- **D3:** Integration test: flat path where image enrichment produces garbled OCR text, verifying garbled blocks have `ocr_text` cleared before persistence. Must FAIL before the fix and PASS after. Decision-log assertion for `post_enrichment_garble_check` event with `stripped_count`/`retained_count` attrs. Additional test: all enriched blocks garbled → all `ocr_text` cleared, document proceeds with zero image-derived text.
- **D4:** Red-green test: construct `TreeGateResult` with `defect=SUSPECT_DENSITY` and `all_defects` containing `SUSPECT_DENSITY`, pass alongside `flat_signals` with sufficient text (`flat_text_len=2151`), assert hard-fail — this test FAILS after the fix. Unit test with divergent tree and flat leaf ratios asserting the flat value drives the verdict. Reorder-inference safety test: flat structure produces `is_reordered=False`. Metadata provenance test: `flat_meta['garble_prongs']` sourced from `_flat_sig`, not `state.gate_result.signals`.
- **D5:** Parameterized unit test (4 cases): `_node_text_parts` with `include_ocr_text=True` includes `ocr_text` but not `summary`; with `include_summary=True` includes `summary` but not `ocr_text`; with both `True` includes both; with both `False` includes neither. Integration test: `TreeSignals.from_tree` produces `flat_text_corrected` that excludes summary. Regression test: density gate `chars_per_page_corrected` does not inflate with summary text. Facade guard updated for BOTH `_node_text_parts` (line 319) AND `_flatten_tree_text` (line 306).
- **D7:** Unit test: Arabic-dominant document uses `_RFC029_MIN_SCANNED_DENSITY_FLOOR_ARABIC` (800) instead of the general floor (1200). Non-Arabic document uses the general floor. Decision event logs `floor_used` and `is_arabic` attrs. Regression: no existing density gate tests break.
- **D8:** Integration test: Arabic document that fails density gate triggers Surya fallback when enabled. Unit test: Surya fallback disabled → no fallback attempt. Unit test: Surya yields sufficient text → density clears, document recovers. Unit test: Surya yields insufficient text → FAIL stands. Decision event `surya_density_fallback` logged with outcome.
- **D9:** Attributed per-document delta table, both directions.

## Implementation Plan

### Sequencing

1. **Wave 1 -- Detector fixes** (D1, D2). Independent of the verdict layer. No verdict movement until D3 wires the consequence. Can be developed in parallel.
2. **Wave 2 -- HR5 consequence** (D3). Depends on Wave 1. Closes the Hard Rule #5 surface.
3. **Wave 3 -- Flat defect re-derivation** (D4). Independent of Waves 1--2. Touches the verdict computation contract; must land alone for attributable corpus delta.
4. **Wave 4 -- Field split** (D5, D6). Independent of Waves 1--3. D6 is a decision gate taken on D5's measurement.
5. **Wave 5 -- Arabic density recovery** (D7, D8). Depends on Waves 1--4 (corrected gate layer). D7 lowers the Arabic floor; D8 adds Surya fallback for documents still failing.
6. **Wave 6 -- Final corpus validation** (D9). Depends on all prior waves. Produces the final attributed delta table and closes RFC-047.

### Effort Estimate

| Wave | Deliverable | Effort | Risk |
|---|---|---|---|
| 1 | D1: `_MIXED_SCRIPT_RE` repair | ~2h | Low -- narrows the regex, cannot introduce new false positives |
| 1 | D2: Ratio threshold in `_garble_check_flat_blocks` | ~3h | Low -- additive config field, existing tests unaffected |
| 2 | D3: HR5 consequence wiring (surgical strip) | ~4h | Medium -- must correctly remove blocks without destabilising indices |
| 3 | D4: Flat-path defect re-derivation | ~4h | **High** -- changes verdict inputs for every flat-routed document |
| 4 | D5: `ocr_text`/`summary` field split | ~3h | Low -- no external callers of the old flag |
| 4 | D6: D8 activation decision | ~2h | Low -- measurement and a recorded decision |
| 5 | D7: Script-aware Arabic density floor | ~2h | Low -- additive config + conditional in existing gate |
| 5 | D8: Surya OCR fallback for Arabic density failures | ~6h | Medium -- new recovery path, HTTP integration with Surya service |
| 6 | D9: Final corpus re-run + attribution table | ~4h | Medium -- long-running; every movement must be explained |
| **Total** | | **~30h** | |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| D3 wired before D1+D2 causes false rejections of clean Arabic documents | **High** if sequencing is violated | Documents with parenthesized markers or digit-adjacent Arabic are rejected | Wave ordering is mandatory; D3's acceptance criteria include a regression test for this case |
| D4 changes the verdict distribution across the entire flat-routed population | **High** -- this is the intended correction | Looks like regression; is actually unmasking | D4 lands alone (Wave 3) so its delta is attributable; unexplained improvements block acceptance |
| D4 activates the reorder-inference branch for flat documents | Medium | Flat documents with `is_reordered=True` would get `REORDERED` hard-fail (previously masked by tree defects) | This is correct behavior -- a reordered flat document should be flagged -- but is a new code path that needs test coverage |
| D5 changes `flat_text_corrected` semantics | Low | Any consumer relying on summary being in `flat_text_corrected` sees fewer characters | The only consumer is the density gate (`gates.py:345`), which explicitly wants OCR-only correction |
| D1 introduces false negatives for digit-only bridging mojibake | Low | A hypothetical mojibake pattern with only digits between Arabic codepoints would be missed | Encoding corruption produces Latin letter substitutions; pure digit bridging is caught by the `digit_ratio` prong (>60% threshold) |
| D2's character-mass threshold lets some garbled enrichments through | Medium | Garbled blocks below the threshold are persisted with metrics logged | The threshold (default 0.10) applies to character mass, not block count; a garbled block holding significant text mass is caught regardless of N; sub-threshold garble metrics are always persisted in `flat_meta` (HR5 compliance) |

## Open Questions

1. **Should D4 pass `None` (approach A) or re-run gate evaluation on the flat structure (approach B)?** Approach (A) is simpler: it says "the flat path has no tree validation result to inherit." Approach (B) is more thorough: it re-derives defects from flat signals by running the `GATE_TABLE` gates against the flat structure. Recommend (A) unless the panel identifies a gate that must fire on flat signals to catch a genuine defect. The key consideration: approach (A) skips `page_count`-dependent checks entirely on the flat path, while approach (B) would need `page_count` threaded through. **The code-correctness reviewer confirmed:** re-running `validate_tree` on flat_structure (approach B) would fire `DEPTH_LOW` on every flat document (depth is always 1) and `NODE_COUNT_LOW` on documents with fewer than 3 blocks — creating new false failures. Approach (A) (`validate_result=None`) is therefore the only correct approach, not merely simpler.

2. **Should D5 add a `flat_text_full` field (both `ocr_text` and `summary`) proactively?** Adding it costs one extra tree walk per document but prevents future callers from re-walking. Recommend deferring until a consumer needs it -- YAGNI.

3. ~~**What is the right `garble_block_ratio_threshold` default for D2?**~~ **RESOLVED (2026-09-22).** Multi-agent review identified that 0.3 breaks `test_single_garbled_block_not_diluted` (ratio 0.2 must condemn; threshold 0.3 would pass it). Corrected to **0.10**. This clears وارد رقم 597 at 0.017, preserves dilution immunity at 0.2, and aligns with the existing `garble_node_ratio_threshold = 0.10` pattern.

## Consequences

- Documents like "وارد رقم 597" that oscillate between FAIL and REJECTED due to the false `sparse_mojibake` prong will stabilise. The prong will stop firing on legitimate Arabic reference numbers and parenthesized bullet markers.
- Post-enrichment garbled OCR content will no longer be silently persisted, closing the Hard Rule #5 surface that the panel identified. Documents with a few garbled chart captions will retain their clean content rather than being fully rejected.
- Flat-routed documents' verdict reasons will describe their flat blocks, not the tree that was already discarded. The uae_numbers portrait and any other document that fails the tree's density gate but clears the flat path's will receive the verdict their flat signals warrant.
- The density-corrected numerator (`flat_text_corrected`) will contain only real OCR text, making D8 activation safe. The 42% inflation from LLM summaries is eliminated.
- The engine-tier decision (successor RFC) will be made against a corpus where four gate-layer bugs have been fixed, ensuring that remaining failures are genuine engine-quality problems rather than plumbing defects.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design | [[design-rfc047-gate-layer-correctness]] |
| Tasks | [[tasks-rfc047-gate-layer-correctness]] |
| Parent investigation | [[RFC-046]] (panel review surfaced all four bugs) |
| Predecessor | [[RFC-045]] (garble chain; `_MIXED_SCRIPT_RE` is in the same subsystem) |
| Successor | RFC-048 (OCR engine tier) -- to be written against D7's residue |
| Evidence: `_MIXED_SCRIPT_RE` over-inclusive | `garble.py:752-755` regex, `garble.py:542-545` usage, memory "Garble PF detector asymmetry" |
| Evidence: `_garble_check_flat_blocks` binary threshold | `garble.py:974` (`if not garbled_count: return None`) |
| Evidence: HR5 fall-through | `indexer.py:1531-1561` (post-enrichment check), `indexer.py:2128-2130` (unreachable guard) |
| Evidence: flat-path defect inheritance | `verdict.py:148-154` (tree defects carried), `verdict.py:167` (signals overridden but not defects), `verdict.py:233` (masked hard-fail on tree defects) |
| Evidence: `ocr_text`/`summary` conflation | `tree_validation.py:110-114` (`include_enrichment` flag), `tree_validation.py:298` (`flat_text_corrected`), `gates.py:345` (density consumer) |
| Evidence: 42% density inflation | uae_numbers portrait document, measured during RFC-046 panel review |
| Memory references | "Garble PF detector asymmetry", "Flat verdict uses tree signals", "Verdict CAS blocks downgrades" |
| Constraints: facade guard | `tests/test_facade_surface_guard.py:306,319` (`_flatten_tree_text`, `_node_text_parts` exports) |
| Constraints: architecture guards | `tests/test_architecture_guards.py` (various OCR and hot-path guards) |
| Decision points registry | `src/pageindex_mcp/obs/decision_points.py` (NOT `helpers/decision_points.py`) |
| Multi-agent review | 2026-09-22: 10-agent workflow (CBM + Serena + Obsidian + specialists), D2 threshold corrected 0.3→0.10, D3 refined to field-clear, D4 red-green test added |