---
zone_name: Measurement and Audit Self-Reinforcing Blind Spot
severity: high
bug_count: 4
status: audited
audit_date: 2026-08-12
audit_run: POST
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
key_files:
  - src/pageindex_mcp/helpers/verdict.py
  - src/pageindex_mcp/storage/verdict.py
  - src/pageindex_mcp/registry/queries.py
tags:
  - zone-spec
  - high
  - measurement-blind-spot
  - self-reinforcing
---
## Mechanism

Self-reinforcing measurement cycle: the pipeline measures content via block.get('text',''), which returns 0 for table blocks. The audit tool built to independently verify the pipeline uses the IDENTICAL block.get('text','') pattern.

A table-heavy document scores 0 chars in BOTH systems simultaneously, making it impossible to tell from the audit alone whether a low score is a real pipeline defect or a shared measurement bug.

The scoring harness process bug (score-stage skipping read_registry_fields) produced null node_count/chars for all 24 documents in its run, silently defaulting to ERROR status — undetected until a later reconciliation caught it.

RFC-025 D4's mandatory pre-publish MinIO re-verification gates one pipeline but does not fix the underlying bug. The fabricated corpus report cascade (Run 9 harness ERROR defaults, RFC-015 verdict fabrication) undermined the corpus quality evidence base.

## Code Evidence

**save_doc_meta** (verdict.py:78-198): _MERGE_FIELDS includes 'total_tree_chars' and 'flat_char_count' derived from block.get('text','').

**upsert_doc** (queries.py:130-184): meta.get('node_count') can be None when scorer does not supply it.

Self-reinforcing pattern: both pipeline's content-volume floor (verdict.py:423-430) and audit scoring use block.get('text','') that ignores table-block content.

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/helpers/verdict.py | Content measurement in promotion |
| src/pageindex_mcp/storage/verdict.py | Metadata merging & persistence |
| src/pageindex_mcp/registry/queries.py | Upsert logic with defaults |

## Related Zones

- [[verdict-gate-cascade]] (promotion uses same text measurement)

---

## Fix Specification (2026-08-29 — Fable code-simplifier consensus)

**Status: APPROVED DESIGN.**

### Stale-evidence correction (important)

The wave-2 fix (`_node_text_parts`, `tree_validation.py:51-86`) already flows through every critical pipeline measurement path. `TreeSignals.flat_text` / `primary_text` are built from the fixed helper at `tree_validation.py:263`, so **the verdict gate already measures through the fixed path** — the `verdict.py:423-430` evidence above is stale. Also fixed transitively: `garble.py:647-651/:684/:694`, `indexer.py:1239` (`total_tree_chars`), `recovery.py:703` (tree side of flat-prefer). The sidecar fields `total_tree_chars` / `flat_char_count` merged by `storage/verdict.py` are now trustworthy.

### What actually remains

1. **One pipeline gap:** `flat.py:178` `_flat_block_primary_text` — a table with headers but zero data rows measures 0 chars (feeds `flat_char_count` and `recovery.py:716` flat-prefer routing). Image blocks measure 0 by design (RFC-027 D0).
2. **The self-reinforcing half is audit-side, not code-side:** no checked-in script uses the naive pattern; the naive sums came from ad-hoc python written *inside audit agent runs* (evidence: `.agents/checkpoints/diagnose-plan-run*/traces.json`). The risk is audit agents re-deriving counts instead of reading the fixed sidecar fields.

### Fix plan (3 steps)

1. **Promote by export, not by move.** Add `_node_text_parts`, `_node_char_count`, `_flat_block_primary_text` to the `helpers/__init__.py` export block (same precedent as `_infer_presentation_forms`). `_node_text_parts` stays in `tree_validation.py`; garble.py's deferred import already handles the circularity. Zero churn; makes the canonical API discoverable.
2. **Close the last pipeline gap** at `flat.py:175-184`: for `role == "table"`, fall back to `headers` when `row_records` is empty. Behavior-affecting only through `recovery.py:716-717` flat-prefer comparison for degenerate header-only tables — hold back if the wave must be provably behavior-neutral.
3. **Break the audit cycle with a doc rule.** Add to `.claude/skills/corpus-ingest-score/SKILL.md` and `.claude/skills/audit-reconcile/SKILL.md`: *"Never measure content volume with `block.get('text')` sums. Read `total_tree_chars` / `flat_char_count` from the sidecar (`read_registry_fields`), or call `helpers._flatten_tree_text` / `helpers._flat_block_primary_text`."* This alone ends the shared-blind-spot cycle.

### Leave alone

- `tree_split.py:423/:570`, `table_stitch.py:152` — `text`-only reads are correct at tree level (tables live as pipe-markdown inside `text` there); "fixing" them risks split/stitch churn for no measurement gain.
- `helpers/flat.py:203` `_flat_search_text` — already handles table/image fields explicitly.
- `verdict.py` — no edits needed; decouples cleanly from the verdict-gate-cascade fix. The single measurement seam is `TreeSignals` construction at `tree_validation.py:263`; do not re-derive there.

### Risk

Steps 1 and 3: measurement/observability only, zero behavior change. Step 2: negligible routing shift possible for header-only tables only.
