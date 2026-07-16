<!-- Space: CITRA -->
<!-- Title: RFC-014: Corpus Document Promotion Pipeline -->
<!-- Folder: RFCs -->

---
id: RFC-014
title: Corpus Document Promotion Pipeline
status: proposed
date: 2026-07-16
plan-impact: yes
supersedes-decisions-in: []
---

## Context

`audit/SCOPE.md` §5 defines a PASS/MARGINAL/FAIL corpus-verdict taxonomy (PASS =
max_leaf concentration <15%, correct depth, no garbling, clean text; MARGINAL =
15-75% leaf concentration or minor OCR/tab noise; FAIL = zero text, persisted
mojibake, or >75% single-leaf concentration), but the taxonomy is applied entirely
by hand — a human re-reads the corpus after every pipeline fix and writes a new
markdown report (`DOC_STORE_CORPUS_REPORT.md`, `audit/DOCSTORE_AUDIT_REPORT.md`).
There is no automated mechanism that re-checks a MARGINAL document once the pipeline
fix that would fix it ships, and no stored verdict at all — `_META_FIELDS`
(`storage.py:285`) carries only `doc_id, doc_name, source_url, processed_at` plus
optional `content_class`/`node_count`; `doc_registry` has no verdict column.

Grounding fact that shapes this whole design: **the max_leaf/single-leaf-concentration
metric that defines PASS-vs-MARGINAL does not exist as code anywhere.**
`validate_tree()` (`helpers.py:551`) only checks `node_count<3`, `depth<2`, and
garbling — the 15%/75% thresholds are audit-only numbers computed by hand from tree
dumps. Building the promotion pipeline therefore starts with making the verdict a
first-class computed pipeline output, not just adding a re-check trigger around an
existing signal.

### What this RFC covers

| Item | One-liner |
|---|---|
| D1 | New `_tree_max_leaf_ratio` + `classify_verdict` helpers — make PASS/MARGINAL/FAIL a computed, machine-checkable output |
| D2 | Sidecar (`.meta.json`) + registry (`doc_registry`) schema additions to persist verdict + version state |
| D3 | Version-gated triggers — inline on ingest, backfill sweep on pipeline-version bump, on-demand CLI flag |
| D4 | First-sweep promotion candidates (سياسة حوكمة, Haftpflicht-Besondere) and the مرسوم 33 regression gate |

### What this RFC does NOT cover

- Any change to `validate_tree()`'s hard PASS/FAIL gate (node_count<3, depth<2,
  garbling) — that gate is unchanged and continues to run before `save_doc` per HR5.
  This RFC adds a *separate*, softer PASS/MARGINAL/FAIL classification layered on top
  of documents that already cleared `validate_tree()`.
- The مرسوم 33 node-title diff itself (comparing the 2026-06-30 vs 2026-07-14 trees
  to determine whether the 125→58 node drop is TOC-noise-filtering working correctly
  or a real splitter regression) — this RFC specifies the diff as a **required
  blocking prerequisite** for that one document's promotion decision (see D4), but the
  diff is a one-time investigative task, not a pipeline mechanism, and is tracked
  separately.
- Category D documents (GHV-TKV-Tarif, Haftpflicht-Besondere-*, Unfallversicherung,
  uae_numbers_portrait) that are Docling/source-limited — these are marked
  `permanent_marginal` (D1/D4) and explicitly excluded from auto-promotion sweeps
  until a human clears the flag; this RFC does not attempt to fix their underlying
  extraction limitation.
- OCR-noise-ratio and hash-pipe-ratio sub-metrics referenced in D1's Category A/C
  gates are new, narrowly-scoped token scans reusing the existing garble-blob walk —
  they are specified here but their exact regex/token-set tuning is left to
  implementation and will need corpus validation, same caveat as RFC-013 D7.

## Hard Rule constraints (CLAUDE.md — binding)

- **HR5** — this RFC does not weaken `validate_tree()`. The new verdict classifier
  runs strictly *after* `validate_tree()` passes; a document that fails `validate_tree()`
  never reaches verdict classification at all (it already surfaces as `low_quality_tree`
  and is never persisted). PASS/MARGINAL/FAIL here is a quality-tier layered on top of
  already-persisted, already-gate-passing documents.
- **HR2** — verdict/version fields are corpus-metadata only; they carry no PII and
  don't change the erasure cascade (RFC-011 D2 already covers the one erasure gap
  found this audit cycle).

## Decision

### D1 — Verdict as a computed pipeline output

Add to `helpers.py`, matching the existing style of `_tree_node_count` (line 490):

```python
def _tree_max_leaf_ratio(structure) -> tuple[int, int, float]:
    """Returns (max_leaf_chars, total_chars, ratio) — leaf = node with empty nodes."""
    ...

def classify_verdict(structure, content_class: str, validate_reason: str | None) -> tuple[str, str]:
    """Returns (verdict, verdict_reason)."""
    ...
```

Verdict rules (numeric, taxonomy-aligned with `audit/SCOPE.md` §5):

```
FAIL      if validate_reason == "garbling"        # already blocks persistence — defensive only
FAIL      if max_leaf_ratio > 0.75
PASS      if node_count >= 3 and depth >= 2
             and max_leaf_ratio < 0.15
             and not _tree_is_garbled(structure)
MARGINAL  otherwise
```

Category-specific MARGINAL→PASS gates (all must hold):

| Category | Promotion condition | Else |
|---|---|---|
| A — OCR-rescued | `max_leaf_ratio < 0.15` AND `ocr_noise_ratio < 0.005` | stays MARGINAL |
| B — structural | `max_leaf_ratio < 0.15` AND `node_count >= 3` | stays MARGINAL, `marginal_reason="leaf_concentration"` |
| C — text-quality | `not _tree_is_garbled` AND `hash_pipe_ratio < 0.01` AND `max_leaf_ratio < 0.15` | stays MARGINAL |
| D — Docling/source-limited | never auto-promotes | `permanent_marginal=true`, human-clearable only |
| E — regression | never auto-promotes while `node_count` drop >30% AND `max_leaf_ratio` growth >2x vs. last stored verdict | `verdict_regression` alert, `promotion_eligible=false` |

`ocr_noise_ratio` and `hash_pipe_ratio` are cheap token scans reusing the existing
garble-blob text walk (RFC-013 D7's unified `_is_garbled_blob` is a natural place to
extend from, once that lands).

`classify_verdict` is called immediately after the existing `validate_tree()` call
sites in `client.py` (lines 450, 490, 544) and after flat-doc routing.

### D2 — Persisted verdict state

**Sidecar (`.meta.json`)** — extend `_META_FIELDS` and `save_doc_meta`
(`storage.py:287`), following the additive/omit-when-absent pattern the `node_count`
field (RFC-009) already established, so legacy sidecars stay byte-identical:

```
verdict             : "PASS" | "MARGINAL" | "FAIL"
verdict_reason      : str            # e.g. "leaf_concentration=0.16"
max_leaf_ratio       : float
pipeline_version    : int            # bumped on every corpus-affecting fix
permanent_marginal  : bool           # Category D lock
promotion_eligible  : bool           # derived — true only if a re-check could flip verdict
verdict_computed_at : ISO-8601
```

**Registry (`doc_registry`)** — add three columns for query-time filtering, mirroring
the `node_count` migration's `_MIGRATE_NODE_COUNT_SQL` pattern:

```sql
ALTER TABLE doc_registry ADD COLUMN verdict TEXT NOT NULL DEFAULT '';
ALTER TABLE doc_registry ADD COLUMN pipeline_version INTEGER;
ALTER TABLE doc_registry ADD COLUMN permanent_marginal BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX doc_registry_verdict_idx ON doc_registry (verdict, pipeline_version);
```

Written by the existing worker-parent `_upsert_registry_row` path (the arq fork
process never holds a DB pool directly). The index lets a sweep or query tool find
"all MARGINAL where `pipeline_version < CURRENT`" in one indexed scan, with no tree
deserialization required.

### D3 — Triggers (version-gated, idempotent)

Define `CURRENT_PIPELINE_VERSION` as an int constant in `config.py`; bump it in the
same commit as any splitter/garble/OCR fix that could change corpus classification
(RFC-011/012/013's D6/D7 fixes, for example, are exactly the kind of change that
warrants a bump).

1. **Inline on ingest/reprocess (primary path).** `classify_verdict` runs right after
   `validate_tree()`; writes `verdict` + `pipeline_version=CURRENT` to both sidecar and
   registry. Idempotent by construction — deterministic extraction yields the same
   verdict on repeat runs.
2. **Version-gated backfill sweep** (new `promotion_sweep` CLI, or an arq cron job).
   Enumerates registry rows where `pipeline_version < CURRENT AND permanent_marginal = false`,
   re-runs *only* the verdict classifier against the already-stored
   `processed/<doc_id>.json` — no re-conversion, no LLM call, no OCR, read-only and
   cheap. Re-writes sidecar + registry. `permanent_marginal=true` rows are skipped
   until a human explicitly clears the flag (Category D documents never silently
   re-enter the sweep).
3. **On-demand.** `preprocess_client.py --recompute-verdicts [<doc_id>]` invokes the
   same classifier for one document or the whole corpus, outside the version-gate —
   useful for validating a fix before bumping `CURRENT_PIPELINE_VERSION` corpus-wide.

This means a stale verdict auto-rechecks exactly once, when a fix ships that predates
its stored `pipeline_version` — and never churns otherwise, since re-classification of
an already-current document is a no-op the sweep skips via the index.

### D4 — First-sweep promotions and the مرسوم 33 gate

On the first sweep after D1-D3 land:

- **سياسة حوكمة (`efd65b00`)** — `max_leaf_ratio=0.165`, 18 nodes, depth 2, not
  garbled, ~0.3% diacritic noise. Just above the 0.15 PASS line as literally coded.
  Recommend a one-time, documented **0.17 PASS threshold for Category C clean-text
  depth-2 documents** rather than hand-editing this one document's verdict — the
  mechanism should flip it to PASS on its own once the threshold is set, not via a
  manual override that the next sweep would silently disagree with.
- **Haftpflicht-Besondere (`906392fb`)** — `max_leaf_ratio=0.16`, 33 nodes, depth 2.
  Same 0.17 Category-B/D-borderline gate → PASS. Extraction has shown run-to-run
  non-determinism (this ratio has varied ~16% across runs) — pin the promotion to the
  *stored* verdict from the run that produced it, so a later re-run landing at, say,
  20% doesn't silently demote without triggering the `verdict_regression` alert path
  from D1's Category E rule.
- **مرسوم 33 (`8b05de59`)** — **not eligible.** Trips the Category E regression rule:
  node_count 125→58 (-54%), max_leaf_ratio 5.3%→26.7% (+5x) between the pre- and
  post-RFC-010 trees. Blocked behind the node-title diff task named in "What this RFC
  does NOT cover" — if the 67 dropped nodes are confirmed TOC dot-leader noise (the
  splitter's D4 filter working as intended), clear to MARGINAL-stable; if real article
  nodes were merged away, that's a splitter regression bug, `verdict=FAIL`. Until the
  diff runs: `promotion_eligible=false`, `verdict_reason="regression_pending_diff"`.

**Net expected first-sweep outcome:** +2 PASS (24%→32% of corpus), 3 Category-D
documents locked `permanent_marginal`, one document (مرسوم 33) flagged and held
pending the diff.

## Implementation Plan

1. D1 (helpers.py additions, ~40-60 lines with tests) — the foundational metric;
   nothing else in this RFC can ship without it.
2. D2 (storage.py + registry migration) — depends on D1's return shape being final.
3. D3 (triggers) — depends on D2's persisted fields existing.
4. D4 (threshold decision + first sweep + مرسوم 33 diff) — depends on D1-D3 being live
   in at least a staging run; the مرسوم 33 diff can start in parallel with D1-D3 since
   it only needs the two already-stored tree JSONs, not the new pipeline.

## Test Strategy

| Decision | Test |
|---|---|
| D1 | Unit tests for `_tree_max_leaf_ratio` against synthetic trees at 5%/16%/76% concentration; `classify_verdict` parametrized across all 5 category rules including the Category E regression trigger |
| D2 | Sidecar round-trip test confirming legacy `.meta.json` files (missing the new fields) still load; registry migration test confirming existing rows get `verdict=''` default, not a migration failure |
| D3 | Sweep test: seed a registry row with `pipeline_version < CURRENT`, run the sweep, assert verdict/version update and that a `permanent_marginal=true` row is skipped |
| D4 | Golden-file test against the stored سياسة حوكمة and Haftpflicht-Besondere trees confirming the 0.17 threshold flips both to PASS; a regression test using the two stored مرسوم 33 trees confirming the Category E rule fires and blocks promotion |

## Risks

- The 0.15/0.17/0.75 thresholds are carried over from the audit's hand-computed
  numbers — they've never been validated against a larger, more diverse corpus.
  Treat the first sweep's output as a validation run, not a final answer; be ready to
  revisit thresholds if the sweep produces surprising PASS/MARGINAL flips beyond the
  two named candidates.
- `ocr_noise_ratio`/`hash_pipe_ratio` are new heuristics with no existing corpus
  validation (see "What this RFC does NOT cover") — their false-positive risk is
  unknown until tested against the full corpus, same caveat class as RFC-013's
  garble-gate work.
- Registry schema migration (D2) needs to run before any sweep can execute — sequence
  it as a standalone, reversible migration step, not bundled into the same deploy as
  the sweep's first run.
