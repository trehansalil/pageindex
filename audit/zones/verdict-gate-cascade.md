---
zone_name: Verdict-Gate Cascade
severity: critical
bug_count: 11
status: fix-spec-approved
audit_date: 2026-08-12
audit_run: POST
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
spec_date: 2026-08-29
spec_provenance: source-verified
key_files:
  - src/pageindex_mcp/helpers/verdict.py
  - src/pageindex_mcp/helpers/types.py
  - src/pageindex_mcp/config.py
  - src/pageindex_mcp/helpers/tree_validation.py
  - src/pageindex_mcp/helpers/gates.py
tags:
  - zone-spec
  - critical
  - verdict-pipeline
  - threshold-tuning
scorecard_verdict: regressed
scorecard_date: 2026-08-12
scorecard_run: POST
---
## Mechanism

First-match-wins promotion pipeline where source-code order IS the specification. Widening a threshold (PASS_MAX_LEAF_RATIO 0.17→0.30) reveals previously-masked defects at the new edge; tightening reveals a different set and regresses previously-passing documents.

The hysteresis band (RFC-025 D0) that widens the leaf-ratio threshold when prior_verdict==PASS combines with interconnected bugs (_script_from_filename returning None, Latin-gibberish heuristic needing expected_script, classify_verdict hardcoding None, threshold widening) to flip previously-FAIL garbled documents to PASS.

Gate racing: when rtl_reversal fires in the terminal-raise list (client.py:1992) BEFORE the flat-path garble gate, the garble gate never executes, making audit conclusions about that gate's coverage unreliable.

## Code Evidence

**apply_promotions** (verdict.py:380-501): ordered if/elif pipeline, six `_try_*` calls at 466-490, D1 hard-fail gate at 450-463.

**Promotion paths**: `_try_image_enrichment` (226-268), `_try_structural_pass` (271-281), `_try_cat_a` (284-293), `_try_cat_b` (296-319), `_try_cat_c` (322-340), `_try_small_doc` (343-366). Aliases at 374-377.

**_clamp_pass** (verdict.py:103-122): caps only on BIDI_DEGRADED and depth-inadequacy.

**_apply_clamp** (verdict.py:430-448): closure; `source_selection` captured from enclosing scope, scoped to `_is_image_enrichment=True`.

**Content-volume floor** (verdict.py:420-428): global, applied before all promotion paths.

**compute_verdict** (verdict.py:504+): thin dispatcher with hard_fail short-circuit.

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/helpers/verdict.py | Promotion pipeline & clamping logic |
| src/pageindex_mcp/helpers/types.py | VerdictThresholds, VerdictResult |
| src/pageindex_mcp/config.py | Threshold constants |
| src/pageindex_mcp/helpers/tree_validation.py | Gate evaluation & override ordering |
| src/pageindex_mcp/helpers/gates.py | Gate definitions & severity |

## Related Zones

- [[garble-detection-kernel]] (interacts via GATE_TABLE severity)
- [[ocr-recovery-cascade]] (recovery gating on narrower set)
- [[measurement-and-audit-self-reinforcing-blind-spot]] (audit inherits this logic)

---

## Fix Specification (2026-08-29, rev 2 — SOURCE-VERIFIED)

> **Provenance.** Rev 1 (Fable code-architect) was retracted: it asserted
> `VerdictContext`, `demotion_guard`, `PromotionResult`, `ocr_garble_ceiling`,
> `matched_paths`, `build_context`, `garble_warn` — all **zero hits** in
> `src/` and `tests/` — and claimed the file was 255 lines. Every line number
> and symbol below was read directly from
> `src/pageindex_mcp/helpers/verdict.py` (593 lines) and
> `src/pageindex_mcp/helpers/types.py`. See
> [[verify-source-before-asserting-defects]].

### Stale claims retired

Two claims carried by the 2026-08-28 triage spec no longer hold against source:

- *"`source_selection` bypasses `_clamp_pass` entirely, letting 38-char docs
  PASS."* — The bypass at verdict.py:443 is already gated on
  `_is_image_enrichment`, and the global content floor at 420-428 rejects any
  doc below `th.min_marginal_chars` before promotion is attempted.
- *"Promotion helpers enforce different content-volume floors or none at all."*
  — A single global floor now precedes every path (420-428).

Also: the six `_try_*` helpers are **already side-effect-free** (they read
`sig`/`th` and return `str | None`; no shared mutable context exists). Any
plan premised on "making the paths pure" is a no-op.

### Verified defects

| ID | Site | Defect |
|----|------|--------|
| **VG-1** | `_try_cat_a` verdict.py:284-293 | **Only path of six with no `effectively_garbled` guard.** The other five check it (246, 279, 312, 335, 359). A garbled `ocr_*` document with `max_leaf_ratio < 0.15` and `ocr_noise_ratio < 0.005` promotes to PASS. Violates HR#5. |
| **VG-2** | `_try_cat_a` verdict.py:291 | Literals `0.15` and `0.005` hardcoded, not on `VerdictThresholds` → absent from the config snapshot, unauditable. |
| **VG-3** | `_try_small_doc` verdict.py:363 | Literals `100` and `15000` hardcoded; the `100` floor is disconnected from `th.min_marginal_chars` (default 50) and silently overrides it. |
| **VG-4** | `VerdictThresholds.from_config` types.py | `hard_fail_max_leaf_ratio=0.75`, `small_doc_leaf_ratio_bound_low=0.20`, `..._high=0.40` are literals rather than `PipelineConfig` fields → invisible to `asdict(pipeline_config)`. |
| **VG-5** | `_try_structural_pass` verdict.py:280 | Returns `""`, so a structural PASS is indistinguishable from any other in `reason`. Telemetry cannot attribute the verdict to a path. |
| **VG-6** | `apply_promotions` verdict.py:466-490 | Only the **winning** path is observable. Nothing records which other paths *also* matched, so a threshold change that re-partitions documents between paths is invisible until a corpus regression surfaces. |
| **VG-7** | verdict.py:452-454 vs 466-468 | `_try_image_enrichment` is invoked twice with identical arguments (each call runs `detect_garble`). |

### The partition surface (verified)

`content_class` already makes three paths mutually exclusive —
`_try_cat_a` requires `ocr_*` (289), `_try_cat_b` requires `flat_*` (302),
`_try_cat_c` requires neither (329). The genuine overlap set is therefore
narrower than "all six" and is exactly:

- **`_try_structural_pass` × everything** — it applies no `content_class`
  filter (271-281), so it shadows every other path whenever structural
  metrics are clean.
- **`_try_image_enrichment` ∩ `_try_cat_b` ∩ `_try_small_doc`** — all three
  admit `flat_*` (image-enrichment narrows to `flat_prose`/`flat_mixed`, 240).

Those two clusters are what the characterization test must pin.

### Phase 1 — this wave

1. **VG-1 (safety, behavior-changing).** Add `not sig.effectively_garbled` to
   the `_try_cat_a` predicate, matching the other five paths.
2. **VG-2/3/4 (behavior-neutral at defaults).** Hoist the literals to
   `PipelineConfig` + `VerdictThresholds`: `cat_a_max_leaf_ratio` (0.15),
   `cat_a_max_ocr_noise` (0.005), `small_doc_min_chars` (100),
   `small_doc_max_chars` (15000), `hard_fail_max_leaf_ratio` (0.75),
   `small_doc_leaf_ratio_bound_low/high` (0.20/0.40). Add an import-time
   assertion `pass_max_leaf_ratio <= hard_fail_max_leaf_ratio`, and
   `small_doc_min_chars >= min_marginal_chars`.
3. **VG-5.** Return `"structural_pass"` instead of `""`. Updates three test
   assertions (`test_verdict.py:425`, `:1448`, `:1462`,
   `test_helpers_combined.py:88`); no production consumer reads the empty
   string.
4. **VG-6.** Evaluate all six paths, collect the matches, and take the first
   in the existing order as the winner — **behavior-identical by
   construction**. Record the full match list on a new
   `VerdictResult.promotion_paths_matched: tuple[str, ...] = ()` field
   (safe: `VerdictResult.__iter__` yields only `(verdict, reason)`, so tuple
   unpacking at every call site is unaffected) and persist it to meta.
5. **VG-7.** Compute `_try_image_enrichment` once before the D1 gate and
   reuse at both 456 and 470.
6. **Partition characterization test** — the piece that breaks the regression
   cycle. A golden table of feature vectors (`content_class`,
   `max_leaf_ratio`, `node_count`, `effectively_garbled`, stripped-text
   length, `image_enrichment_ratio`, `inspector_class`, defect set) →
   expected `(promotion_paths_matched, winner, verdict)`. A future threshold
   change that shifts documents between paths then fails CI with a readable
   diff instead of surfacing as a corpus regression waves later.

**Risk.** Only VG-1 changes verdicts: garbled OCR documents that previously
promoted via cat_a now fall through to MARGINAL/FAIL. That is the correct
direction under HR#5, but a **corpus re-run is mandatory before merge**.
Items 2-6 are behavior-neutral at default configuration.

### Phase 2 — deferred

Once the characterization table has baselined the corpus, consider promoting
the clamp step to an always-evaluated floor phase that runs before path
selection (rather than inside `_apply_clamp` per winner), so no path can be
routed around a safety cap. Do not attempt this in the same wave as Phase 1 —
the baseline must exist first.

### Rejected approaches

- **`PromotionSpec` / `PROMOTION_REGISTRY`** (the 2026-08-28 triage spec) —
  it preserves evaluation order by construction via `PromotionSpec.priority`
  and keeps `source_selection` as a bypass, so it addresses none of VG-1,
  VG-5, VG-6. It also carries the two stale premises retired above. Its one
  genuinely useful item, the `hard_fail_max_leaf_ratio` config hoist, is
  absorbed here as VG-4.
- **Another round of intra-path predicate/threshold tuning** — the process
  that produced the 5→8→11 trajectory.
- **Score/weight voting across paths** — destroys per-path attribution, which
  HR#5 auditability depends on.
- **Negative guards to force mutual exclusion** — combinatorial growth, and
  threshold tweaks still re-partition silently.
- **"Highest verdict wins" without a separate demotion phase** — would let a
  structural PASS outvote a garble demotion.
