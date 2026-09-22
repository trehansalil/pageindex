---
id: design-rfc047-gate-layer-correctness
title: "Design: Gate-Layer Correctness"
type: design
status: draft
date: 2026-09-22
tags:
  - design
  - gate-correctness
  - verdict
  - garble-detection
  - density-gate
  - flat-routing
aliases:
  - design-rfc047-gate-layer-correctness
governs:
  - "[[RFC-047]]"
---
# Design Document: Gate-Layer Correctness

## Traceability

| Artifact | Reference |
|----------|-----------|
| Governing RFC | [[047-gate-layer-correctness]] |
| Architecture Doc | [[ARCHITECTURE]] |
| Implementation Plan | [[tasks-rfc047-gate-layer-correctness]] |
| Parent Investigation | [[RFC-046]] (panel review surfaced all four bugs) |
| Predecessor | [[RFC-045]] (garble chain; `_MIXED_SCRIPT_RE` is in the same subsystem) |
| Panel Review | [[rfc046-post-wave7-panel-review]] |
| 8.1 Characterisation | [[rfc046-wave8-8-1-surviving-failures]] |

## Design Scope

This document covers the implementation design for RFC-047's seven deliverables (D1–D7), focusing on code-level changes, API contracts, decision-point registration, config surface, and test architecture.

All four code bugs share a common shape: a gate or detector produces a result and the layer that consumes it either conflates inputs, ignores the result, inherits a stale result from a different evaluation, or over-matches on a syntactically broad pattern.

---

## D1: `_MIXED_SCRIPT_RE` Repair

### Site

**File:** `src/pageindex_mcp/helpers/garble.py`
**Lines:** 752–755 (regex definition), 542–545 (usage in `_garble_prongs`)

### Current regex

```python
_MIXED_SCRIPT_RE = re.compile(
    r"[؀-ۿ][\x21-\x7E]{1,8}[؀-ۿ]"
    r"|[\x21-\x7E]{1,8}[؀-ۿ][\x21-\x7E]{1,8}"
)
```

The `[\x21-\x7E]` class covers ALL printable ASCII — digits `0-9` (`\x30`–`\x39`), parentheses, periods, commas, and Latin letters alike. This matches legitimate Arabic reference numbers (`رقم597`), parenthesized Arabic bullet markers (`(أ)`, `(ب)`), and article references (`المادة15`).

### Design

Replace with a regex requiring at least one ASCII letter `[A-Za-z]` in the bridging run. The second alternation is split into two branches requiring a letter on EITHER side (OR logic, not AND), addressing the reviewer finding that AND logic creates a false-negative gap:

```python
_MIXED_SCRIPT_RE = re.compile(
    # Alt 1: Arabic + (ASCII run containing ≥1 letter) + Arabic
    r"[؀-ۿ](?=[\x21-\x7E]{0,7}[A-Za-z])[\x21-\x7E]{1,8}[؀-ۿ]"
    # Alt 2: letter-led ASCII run + Arabic + any ASCII run
    r"|[A-Za-z][\x21-\x7E]{0,7}[؀-ۿ][\x21-\x7E]{1,8}"
    # Alt 3: any ASCII run + Arabic + ASCII run containing letter
    r"|[\x21-\x7E]{1,8}[؀-ۿ][\x21-\x7E]{0,7}[A-Za-z]"
)
```

### Rationale

True mojibake from broken Arabic encoding produces Latin letter substitutions (`كtابcجديد` — Latin letters `t`, `c` glued between Arabic). Legitimate Arabic text has digits and punctuation adjacent but no Latin letters. A hypothetical mojibake pattern bridged ONLY by digits (no letters) is adequately caught by the `digit_ratio` prong (>60% threshold) or `numeric_junk_short` (>90% for short text).

### Test plan

**Existing tests (pass unchanged):** All six assertions in `TestSparseMojibake`, `TestSparseMojibakeDetection`, and `test_rfc_quality.py:315` — their fixtures contain ASCII letters (`x`, `z`, `q`, `k`, `X`, `Y`, `Z`) within the bridging runs.

**New tests:**
- Parenthesized Arabic letters `(أ)` must NOT fire `sparse_mojibake`
- Digit-only bridging `رقم597` must NOT match
- Realistic Arabic insurance document with section markers must remain clean

---

## D2: `_garble_check_flat_blocks` Ratio Threshold

### Site

**Files:**
- `src/pageindex_mcp/helpers/garble.py` (`_garble_check_flat_blocks`, `GarbleConfig`)
- `src/pageindex_mcp/helpers/pipeline_config.py` (`PipelineConfig`)

### Current behaviour

```python
if not garbled_count:
    return None
# garbled_count >= 1 → return GarbleReport(is_garbled=True, ...)
```

Binary: one garbled block out of any number condemns the entire enrichment. The ratio is computed (`garble_ratio = garbled_count / checked_count`) and logged in decision attrs, but never compared to a threshold.

### Design

1. Add a module-level constant `_GARBLE_BLOCK_RATIO_THRESHOLD = 0.10` at `garble.py:~764`, following the pattern of `_RFC029_DEEP_TREE_DEPTH_THRESHOLD`. Promote to `GarbleConfig` only when a second consumer or deployment-specific override appears (YAGNI).
2. Change `_garble_check_flat_blocks` return logic to use **character-mass ratio** instead of block-count ratio:

```python
# Accumulate character counts alongside garble verdicts
garbled_chars = 0
total_chars = 0
for block in blocks:
    text = block_text(block, BlockTextPurpose.CHAR_COUNT)
    if not text or not text.strip():
        continue
    checked_count += 1
    block_len = len(text)
    total_chars += block_len
    report = detect_garble(text, ...)
    if report:
        garbled_count += 1
        garbled_chars += block_len
        all_fired.update(report.fired_prongs)

# Character-mass ratio: garbled characters / total characters
_char_ratio = garbled_chars / total_chars if total_chars else 0.0
if _char_ratio < _GARBLE_BLOCK_RATIO_THRESHOLD:
    decision(
        event="garble_flat_block_verdict",
        choice="below_threshold",
        reason="garbled_char_mass_below_ratio_threshold",
        attrs={
            "checked_count": checked_count,
            "garbled_count": garbled_count,
            "garbled_chars": garbled_chars,
            "total_chars": total_chars,
            "char_mass_ratio": _char_ratio,
            "threshold": _GARBLE_BLOCK_RATIO_THRESHOLD,
            "fired_prongs": sorted(all_fired),
        },
        logger=logger,
    )
    return None
```

### Post-gate-FAIL correction: character-mass ratio (2026-09-22)

The Wave 1 gate FAIL demonstrated that using **block count** as the denominator (`garbled_blocks / total_blocks`) disables single-block garble detection for every document above 10 blocks (1/N < 0.10 when N > 10). The real corpus has 198–297 blocks per document, so the gate was effectively off. The corrected approach computes `garbled_chars / total_chars` — summing `len(block_text(b))` for garbled and total blocks respectively. This catches a single garbled block holding 60% of a document's characters while tolerating a tiny garbled caption among hundreds of clean blocks.

### Threshold choice (corrected 2026-09-22)

Default **0.10** (not 0.3 — the original proposal was refuted by multi-agent review). `test_single_garbled_block_not_diluted` (`tests/test_garble.py:1134`) creates 5 blocks with 1 garbled (ratio 0.2) and asserts condemnation. A threshold of 0.3 would violate the RFC-026/RFC-027 dilution immunity invariant. The value 0.10 applied to **character mass** clears a small garbled caption (low char mass) while catching a large garbled block (high char mass).

### Tri-caller impact (corrected 2026-09-22)

`_garble_check_flat_blocks` is called from **three** sites — the main garble gate (`indexer.py:1409`), the **VLM-fallback recovery check (`indexer.py:1451`)**, and the post-enrichment gate (`indexer.py:1532`). All three pass the **identical** config expression `_image_garble_cfg if _image_garble_cfg is not None else _garble_config` — the claimed per-caller differentiation does not exist. All three become character-mass-ratio-aware. Per-caller config is deferred to a future investigation.

### VLM site alignment (2026-09-22)

The VLM-fallback site (`indexer.py:1451`) uses the same character-mass ratio approach as the main and post-enrichment sites. The 0.10 threshold applies uniformly. VLM output should be clean; if the vision model produces garbled text above the threshold, the recovery is correctly marked as failed.

### flat_meta garble persistence (2026-09-22)

Sub-threshold garble metrics MUST be persisted into `flat_meta` regardless of whether the condemnation threshold was crossed. Currently, below-threshold garble prongs are dropped from `flat_meta['garble_prongs']` — the document is persisted with no trace that garble was detected. This violates CLAUDE.md Hard Rule #5 (never silently persist a low-quality tree). The fix: always write `flat_meta['garble_ratio']` and `flat_meta['garble_prongs']` from the flat-blocks check. At `indexer.py:1650-1653`, source these from the flat check result rather than from `state.gate_result.signals.garble_prongs` (which reflects the tree, not the flat path).

### Test plan

- **Character-mass critical test:** 1 garbled block holding 60% of characters among 297 clean blocks → char-mass ratio ~0.60 → CONDEMNED (this is the bug the block-count approach missed)
- **Large-N survival:** 1 garbled block among 198 blocks where garbled block is small (low char mass, e.g. a caption) → char-mass ratio well below 0.10 → NOT condemned
- Parameterized unit test (3 cases): garbled char mass above 0.10 → condemned; garbled char mass below 0.10 → clean; zero garbled → clean
- Regression: `test_single_garbled_block_not_diluted` still passes (1 garbled of 5 with significant char mass → condemned)
- Regression: one garbled chart caption among 10+ clean blocks does NOT trigger rejection
- **flat_meta persistence:** sub-threshold garble ratio and prongs are written to `flat_meta` even when verdict is "below threshold"
- Constant: `_GARBLE_BLOCK_RATIO_THRESHOLD` defaults to 0.10 at module level (now applied to character mass)

---

## D3: HR5 Post-Enrichment Garble Consequence Wiring

### Site

**File:** `src/pageindex_mcp/client/indexer.py`
**Lines:** 1531–1561 (post-enrichment garble check), 2128–2130 (`flat_garble_unrecovered_reject` guard)

### Current behaviour

After image blocks are enriched with OCR text (line 1519, `_apply_picture_enrichment`), the code runs `_garble_check_flat_blocks` on the enriched blocks (line 1532). When garble is detected, a decision event `post_enrichment_garble_check` is logged with choice `enriched_blocks_garbled` — and then execution falls through. The garbled OCR content is persisted. This violates CLAUDE.md Hard Rule #5.

The existing reject guard at lines 2128–2130 checks `state.flat_garble_unrecovered`, which is set only by the MAIN flat-blocks garble gate (line 1415). The post-enrichment garble result is never written into any state field that reaches the reject guard.

### Design

Surgical field-clear (refined 2026-09-22): replace the `_garble_check_flat_blocks` call at the post-enrichment site with an inline per-block loop. When the garble ratio exceeds the D2 threshold, clear `ocr_text` on each garbled image block rather than removing blocks from the list. This preserves image metadata (`figure_path`, `page`, `bbox`), avoids list mutation and insertion-point sensitivity.

**Insertion point:** After line ~1554 (decision log) and before line ~1580 (`flat_structure` construction).

```python
if _enrich_garble:
    decision(
        event="post_enrichment_garble_check",
        choice="enriched_blocks_garbled",
        ...
    )
    # D3: clear ocr_text on garbled blocks instead of falling through
    _stripped = 0
    for block in _enriched_image_blocks:
        if detect_garble(block_text(block), config=_image_garble_cfg).is_garbled:
            block["ocr_text"] = ""
            _stripped += 1
    decision(
        event="post_enrichment_garble_check",
        choice="blocks_stripped" if _stripped else "strip_skipped",
        reason="garbled enrichment blocks cleared before persistence",
        attrs={
            "stripped_count": _stripped,
            "retained_count": len(_enriched_image_blocks) - _stripped,
            "garble_ratio": _stripped / len(_enriched_image_blocks),
        },
    )
```

**Edge case — all blocks garbled:** When ALL enriched image blocks are garbled (ratio = 1.0), all `ocr_text` fields are cleared. The document proceeds with zero image-derived text but retains its image metadata and non-image content.

### Decision point update

Extend the existing `post_enrichment_garble_check` event in `decision_points.py` (`src/pageindex_mcp/obs/decision_points.py`) with `stripped_count`/`retained_count`/`garble_ratio` attrs and add `blocks_stripped`/`strip_skipped` to its choices, rather than registering a separate event.

### Dependencies

**MUST** land after D1 (regex repair) and D2 (ratio threshold). Without D1, the regex false-positives on legitimate Arabic patterns. Without D2, a single garbled chart caption condemns all enriched blocks.

### Test plan

- Integration: flat path where image enrichment produces garbled OCR text → garbled blocks stripped, clean blocks persisted
- Regression: document with 1 garbled chart caption among 10+ clean image blocks retains all clean content
- Decision event: `post_enrichment_garble_strip` logged with correct `stripped_count`

---

## D4: Flat-Path Defect Re-Derivation

### Site

**Files:**
- `src/pageindex_mcp/client/indexer.py` (~line 1596, `compute_verdict` call site)
- `src/pageindex_mcp/helpers/verdict.py` (`evaluate_gates`, lines 123–277)

### Current behaviour

When a document fails as a tree and is retried as flat blocks, the call site passes `state.gate_result` (the TREE's `TreeGateResult`) to `compute_verdict`. Inside `evaluate_gates`:

- `defect` and `_all_defects` are sourced from `validate_result` (the TREE evaluation)
- `sig` is overridden with `flat_signals` (the FLAT signals) at lines ~167–168
- Defects describe the tree; signals describe the flat blocks — **provenance mismatch**

At line ~233, `_all_defects & HARD_FAIL_DEFECTS` fires on the tree's `SUSPECT_DENSITY`, condemning a flat document whose flat signals clear the density gate.

### Design

**Approach A (recommended):** Pass `None` as `validate_result` on the flat verdict path.

```python
# indexer.py, flat verdict call site (~line 1596)
# Before:
state.gate_result,
# After:
None,  # flat path: no tree validation result to inherit
```

This makes `evaluate_gates` enter the `validate_result is None` branch (verdict.py:187–200), where `defect = TreeDefect.OK` and `_all_defects = frozenset()`. All hard-fail defects are absent, and the flat signals alone drive the verdict.

The existing `reordered_defect_inferred` event at `verdict.py:192-200` already fires with `validate_result_present=False` when the `None` branch is taken, providing audit trail. If tree defect audit is needed, add tree defect values as attrs to the existing event rather than a separate event.

**Metadata provenance fix:** After D4 passes `None`, source `flat_meta['garble_prongs']` (at `indexer.py:1650-1653`) from `_flat_sig` (already computed at line ~1590) rather than from `state.gate_result.signals.garble_prongs`, maintaining provenance consistency between the flat verdict and its metadata.

### Why NOT approach B (re-run validate_tree on flat_structure)

Re-running `validate_tree` on `flat_structure` would fire:
- `DEPTH_LOW` — flat depth is always 1 (< 2 threshold at `gates.py:62`)
- `NODE_COUNT_LOW` — if fewer than 3 blocks (threshold at `gates.py:51`)

This would create new false failures on every flat document with shallow structure.

### Verdict movement

Documents that currently FAIL on the flat path due to inherited tree defects will receive the verdict their flat signals produce. For the uae_numbers portrait: `FAIL` → clears density gate → proceeds to Phase 2 promotions → expected `PASS` or `MARGINAL`.

### Risk: reorder-inference branch activation

With tree defects absent, the flat path may hit the reorder-inference branch for flat documents with `is_reordered=True`. Flat structures currently never carry `start_index`/`line_num`, so `is_reordered` is always `False` — but this safety is an implicit contract. This is correct behaviour (a reordered flat document should be flagged), but is a new code path that needs test coverage.

### Test plan

- **Red-green test (mandatory):** Construct `TreeGateResult` with `defect=SUSPECT_DENSITY` and `all_defects` containing `SUSPECT_DENSITY`, pass alongside `flat_signals` with sufficient text (`flat_text_len=2151`), assert hard-fail. This test FAILS after the fix (passing `None` removes the hard-fail).
- Unit: flat document whose tree had `SUSPECT_DENSITY` but with sufficient flat text does NOT receive FAIL (post-fix assertion)
- Unit: divergent tree and flat leaf ratios — flat value drives the verdict
- **Reorder-inference safety:** Flat structure produces `is_reordered=False`; `evaluate_gates` with `None` validate_result and `is_reordered=False` produces `defect=TreeDefect.OK`
- **Metadata provenance:** `flat_meta['garble_prongs']` sourced from `_flat_sig`, not `state.gate_result.signals`
- Regression: existing PASS flat documents remain PASS

---

## D5: `ocr_text`/`summary` Field Split

### Site

**File:** `src/pageindex_mcp/helpers/tree_validation.py`
**Lines:** 110–114 (`_node_text_parts`), 298 (`flat_text_corrected` construction), 281 (`TreeSignals.flat_text_corrected` field)
**Consumer:** `gates.py:345` (density gate)

### Current behaviour

`_node_text_parts` uses a single `include_enrichment` flag that appends BOTH `summary` (LLM-generated abstract) and `ocr_text` (real OCR from image blocks). Including LLM `summary` inflates the density numerator by 42% on the uae_numbers portrait document, poisoning the corrected value.

### Design

Replace the single boolean with two independent flags:

```python
def _node_text_parts(
    n: dict,
    *,
    include_ocr_text: bool = False,
    include_summary: bool = False,
) -> list[str]:
    ...
    if include_ocr_text:
        value = str(n.get("ocr_text", ""))
        if value and value not in parts:
            parts.append(value)
    if include_summary:
        value = str(n.get("summary", ""))
        if value and value not in parts:
            parts.append(value)
```

Propagate through `_flatten_tree_text` (same two flags). In `TreeSignals.from_tree` (line 298):

```python
# Before:
flat_text_corrected = _flatten_tree_text(structure, include_enrichment=True)
# After:
flat_text_corrected = _flatten_tree_text(structure, include_ocr_text=True, include_summary=False)
```

### Facade guard update

`tests/test_facade_surface_guard.py` exports BOTH `_node_text_parts` (line 319) AND `_flatten_tree_text` (line 306). Both functions' signatures change in lockstep — the guard must be updated for both, not only `_node_text_parts`.

### Dedup behavior note

`_node_text_parts` lines 113-114 silently drop `ocr_text`/`summary` values that duplicate body text. This behavior is preserved after the split and prevents inflation from duplicated content. Implementation must not change this dedup check.

### Downstream impact

The density gate at `gates.py:345` continues to use `flat_text_corrected`, which now contains only OCR text augmentation. This makes D8 activation safe — switching the gate to fire on the corrected value is unblocked once the corpus measurement confirms the split numerator produces correct results.

### Test plan

- Unit: `_node_text_parts` with `include_ocr_text=True` includes `ocr_text` but NOT `summary`
- Unit: `_node_text_parts` with `include_summary=True` includes `summary` but NOT `ocr_text`
- Unit: `_node_text_parts` with both `True` includes both
- Integration: `TreeSignals.from_tree` produces `flat_text_corrected` excluding summary
- Regression: density gate `chars_per_page_corrected` does not inflate with summary text
- Facade guard: updated for new parameter names

---

## D6: D8 Activation Decision (Decision Gate)

No code change. This is a measurement-driven decision gate:

1. D5 SHALL have landed and a corpus run SHALL have been taken with the split numerator
2. A per-document table SHALL show old `chars_per_page_corrected` (summary + ocr_text) vs new value (ocr_text only), and which documents would change verdict
3. The decision SHALL be recorded with a dated entry and the measurement

---

## D7: Full Corpus Re-Run + Engine Decision

Full corpus run after D1–D5 have landed. Attributed per-document delta table.

1. Every verdict movement attributed to a named deliverable (D1, D2, D3, D4, or D5)
2. Movements reported in both directions — improvements and regressions
3. Unexplained movements block acceptance pending investigation
4. Residue determines whether the engine-tier RFC (RFC-048) is written

---

## Cross-Cutting Concerns

### Decision Events

| Event | Status | Deliverable |
|-------|--------|-------------|
| `garble_flat_block_verdict` | Existing — new choice `below_threshold` added | D2 |
| `post_enrichment_garble_check` | Existing — extended with `blocks_stripped`/`strip_skipped` choices and `stripped_count`/`retained_count`/`garble_ratio` attrs | D3 |
| `reordered_defect_inferred` | Existing — `validate_result_present=False` signals the `None` branch | D4 (audit trail) |
| `suspect_density_gate` | Existing — unchanged | D6 (activation decision) |

### Config Surface

| Field | Location | Default | Deliverable |
|-------|----------|---------|-------------|
| `_GARBLE_BLOCK_RATIO_THRESHOLD` | Module-level constant in `garble.py:~764` | `0.10` (applied to **character mass**, not block count) | D2 |

### Facade Guards

| Symbol | Guard file | Deliverable |
|--------|-----------|-------------|
| `_node_text_parts` | `test_facade_surface_guard.py:319` | D5 |
| `_flatten_tree_text` | `test_facade_surface_guard.py:306` | D5 |

### Backward Compatibility

| Deliverable | Direction | Risk |
|-------------|-----------|------|
| D1 | Narrows regex matches — fewer matches, never more | Cannot introduce new false positives |
| D2 | Raises bar for garble condemnation — some previously flagged may pass | Correct behaviour |
| D3 | New field-clear path — additive, preserves block metadata | No backward concern |
| D4 | Changes verdict inputs for ALL flat-routed documents | **Highest risk** — must land alone |
| D5 | Changes `flat_text_corrected` semantics | Only consumer is density gate |

### Relationship to RFC-046 Open Tasks

| RFC-046 Task | Disposition |
|--------------|-------------|
| 4.4 (garble-check enrichment-mutated blocks) | **Subsumed by D3** — D3 is the consequence wiring that 4.4 identified as missing |
| 5.3 (reconcile the two arbitrators) | **Not addressed** — deferred; belongs in a future RFC covering the multi-engine framework |

### C6 Latent Defect

C6 (garble-primary masks flat lifeboat) remains **latent and unaddressed** by this RFC. `decide_route` maps `GARBLING → RETRY_OCR → Route.TREE` unconditionally (`types.py:371–372`). A document whose tree-level garble is not resolved by D4 is still routed away from the flat lifeboat. This is explicitly a non-goal: fixing C6 requires changing the `decide_route` contract, which is RFC-044 Phase B scope. If D7's corpus residue shows C6-attributed failures, the engine-tier successor RFC inherits it.

### ENGINE_RELIABILITY_ORDER

`arbitrate.py:28–32` lists `surya`, `paddleocr`, `paddleocr-vl` — names that appear nowhere else in `src/`. This is dead multi-engine framework code. Not addressed by this RFC; documented as the dead-code cleanup for the engine-tier successor.
