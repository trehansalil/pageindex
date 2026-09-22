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

1. Add `garble_block_ratio_threshold: float = 0.3` to `GarbleConfig` (garble.py ~line 613)
2. Add matching field to `PipelineConfig` with `from_config` wiring (following the pattern of `garble_node_ratio_threshold`)
3. Change `_garble_check_flat_blocks` return logic:

```python
_ratio = garbled_count / checked_count if checked_count else 0.0
if _ratio < config.garble_block_ratio_threshold:
    decision(
        event="garble_flat_block_verdict",
        choice="below_threshold",
        reason="garbled_blocks_below_ratio_threshold",
        attrs={
            "checked_count": checked_count,
            "garbled_count": garbled_count,
            "garble_ratio": _ratio,
            "threshold": config.garble_block_ratio_threshold,
            "fired_prongs": sorted(all_fired),
        },
        logger=logger,
    )
    return None
```

### Threshold choice

Default 0.3 (fewer than 30% of blocks garbled is tolerated). This is above وارد رقم 597's 0.017 (5/297) and below any reasonable majority threshold. Validated against the corpus before D3 wires the consequence.

### Test plan

- Unit: `_garble_check_flat_blocks` with 1 garbled block out of 10 returns `None` at threshold 0.3
- Unit: 4 garbled blocks out of 10 returns a `GarbleReport` at threshold 0.3
- Regression: one garbled chart caption among 10+ clean blocks does NOT trigger rejection
- Config: `GarbleConfig.garble_block_ratio_threshold` defaults to 0.3 and is overridable

---

## D3: HR5 Post-Enrichment Garble Consequence Wiring

### Site

**File:** `src/pageindex_mcp/client/indexer.py`
**Lines:** 1531–1561 (post-enrichment garble check), 2128–2130 (`flat_garble_unrecovered_reject` guard)

### Current behaviour

After image blocks are enriched with OCR text (line 1519, `_apply_picture_enrichment`), the code runs `_garble_check_flat_blocks` on the enriched blocks (line 1532). When garble is detected, a decision event `post_enrichment_garble_check` is logged with choice `enriched_blocks_garbled` — and then execution falls through. The garbled OCR content is persisted. This violates CLAUDE.md Hard Rule #5.

The existing reject guard at lines 2128–2130 checks `state.flat_garble_unrecovered`, which is set only by the MAIN flat-blocks garble gate (line 1415). The post-enrichment garble result is never written into any state field that reaches the reject guard.

### Design

Surgical strip (proportionate response): when `_enrich_garble` is truthy and the garble ratio exceeds the D2 threshold, strip the garbled image blocks from the blocks list before persistence. Log which blocks were stripped.

```python
if _enrich_garble:
    decision(
        event="post_enrichment_garble_check",
        choice="enriched_blocks_garbled",
        ...
    )
    # D3: strip garbled blocks instead of falling through
    _garbled_block_ids = {id(b) for b in _enriched_image_blocks if ...}
    _pre_strip_count = len(blocks)
    blocks = [b for b in blocks if id(b) not in _garbled_block_ids]
    decision(
        event="post_enrichment_garble_strip",
        choice="blocks_stripped",
        reason="garbled enrichment blocks removed before persistence",
        attrs={
            "stripped_count": _pre_strip_count - len(blocks),
            "remaining_count": len(blocks),
        },
    )
```

### Decision point registration

New event in `decision_points.py`:

```python
"post_enrichment_garble_strip": DecisionPoint(
    event="post_enrichment_garble_strip",
    choices=("blocks_stripped", "strip_skipped"),
    note="D3 consequence: garbled enrichment blocks stripped before persistence",
    attrs=("stripped_count", "remaining_count"),
)
```

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

Add a decision event recording the tree gate discard:

```python
decision(
    event="flat_verdict_tree_gate_discarded",
    choice="tree_gate_discarded",
    reason="flat path does not inherit tree defects",
    attrs={"tree_defect": str(state.gate_result.defect) if state.gate_result else None},
)
```

### Why NOT approach B (re-run validate_tree on flat_structure)

Re-running `validate_tree` on `flat_structure` would fire:
- `DEPTH_LOW` — flat depth is always 1 (< 2 threshold at `gates.py:62`)
- `NODE_COUNT_LOW` — if fewer than 3 blocks (threshold at `gates.py:51`)

This would create new false failures on every flat document with shallow structure.

### Decision point registration

New event in `decision_points.py`:

```python
"flat_verdict_tree_gate_discarded": DecisionPoint(
    event="flat_verdict_tree_gate_discarded",
    choices=("tree_gate_discarded",),
    note="D4: flat verdict does not inherit tree defects",
    attrs=("tree_defect",),
)
```

### Verdict movement

Documents that currently FAIL on the flat path due to inherited tree defects will receive the verdict their flat signals produce. For the uae_numbers portrait: `FAIL` → clears density gate → proceeds to Phase 2 promotions → expected `PASS` or `MARGINAL`.

### Risk: reorder-inference branch activation

With tree defects absent, the flat path may hit the reorder-inference branch for flat documents with `is_reordered=True`. This is correct behaviour (a reordered flat document should be flagged), but is a new code path that needs test coverage.

### Test plan

- Unit: flat document whose tree had `SUSPECT_DENSITY` but with sufficient flat text does NOT receive FAIL
- Unit: divergent tree and flat leaf ratios — flat value drives the verdict
- Decision event: `flat_verdict_tree_gate_discarded` logged with correct `tree_defect`
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

`tests/test_facade_surface_guard.py` exports `_node_text_parts` (line 319) and `_flatten_tree_text` (line 306). The signature change requires the guard assertions to match the new parameter names.

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
| `post_enrichment_garble_check` | Existing — unchanged | — |
| `post_enrichment_garble_strip` | **New** | D3 |
| `flat_verdict_tree_gate_discarded` | **New** | D4 |
| `suspect_density_gate` | Existing — unchanged | D6 (activation decision) |

### Config Surface

| Field | Location | Default | Deliverable |
|-------|----------|---------|-------------|
| `garble_block_ratio_threshold` | `GarbleConfig` + `PipelineConfig` | `0.3` | D2 |

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
| D3 | New strip path — additive | No backward concern |
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
