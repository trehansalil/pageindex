# Dead-Code Audit — 2026-09-06 (Run 3)

- **Workflow:** `dead-code-discover-verify` (run `wf_288eba18-f22`)
- **Base HEAD:** `0f9ad5e` · **Branch:** `ICR-97-rfc44-recovery-dispatch-wiring`
- **Phases:** Discover (7 lenses) → Verify → Refute (adversarial skeptic) → Critique (+ cascade round)
- **Cost:** 20 agents, 554 tool calls, 1.79M tokens, 38 min wall clock
- **Supersedes:** `DEAD_CODE_AUDIT_2026-09-05.md` for the items listed below.

## 1. Result summary

| Bucket | Count |
|---|---|
| Lenses reporting | 7 / 7 |
| Unique candidates | 15 |
| Confirmed dead | 13 rows → **11 distinct symbols** |
| Refuted by skeptic | 2 |
| Unsure | 0 |
| Alive | 2 |
| **Applied in this pass** | **10 symbols + 5 cascade imports** |

Zones: helpers=4, client=1, core=1, converters=2, tests=7 → 5 verification groups.

## 2. Applied removals

| # | File | Symbol | Kind | Cascade also removed |
|---|---|---|---|---|
| 1 | `src/pageindex_mcp/helpers/heuristic_registry.py:103` | `HeuristicRegistry.all_entries` | method | — |
| 2 | `src/pageindex_mcp/config.py:79` | `Settings.doc_store_path` | config field | `:279` kwarg, `:272` `repo_root`, `:6` `from pathlib import Path` |
| 3 | `src/pageindex_mcp/picture_plane.py:98` | `SkipReason.counts_in_enrichment_denominator` | property alias | — |
| 4 | `tests/test_bidi.py:1584` | `_chain_names` | test helper | orphaned D4 section banner |
| 5 | `tests/test_bidi.py:1662` | `_slow_chunk_worker` | test helper | `:11 import time`, worker-stand-in comment |
| 6 | `tests/test_bidi.py:1669` | `_fast_chunk_worker` | test helper | — |
| 7 | `tests/test_bidi.py:50` | duplicate `_inject_arabic_structural_headings` import | import (F811) | — |
| 8 | `tests/test_converters.py:1483` | `_ledger_response` | test helper | `:6 import json` |
| 9 | `tests/test_converters.py:1947` | `_pic` | test helper | `PictureResult` name from `:25` import block |
| 10 | `tests/test_converters.py:2170` | `_garbling_without_exception_gate` | test helper | `:19 import pageindex_mcp.client as client_mod` |
| 11 | `tests/test_converters.py:2226` | `_standalone_image_ocr_should_run` | test helper | `:22 MIN_STANDALONE_IMAGE_MD_CHARS` import |

Note: rows 1 and 2 of the workflow's `confirmed` array were the **same symbol**
(`all_entries`), raised independently by the graph-degree and docs-history lenses.
Collapsed to one deletion.

Each cascade import was independently re-verified before removal:

- `import time` (test_bidi) — apparent uses at `:1549`/`:1563` are inside the string
  literal `"pageindex_mcp.storage.minio_ops.time.sleep"`, not real uses.
- `PictureResult` — `:765` is a docstring mention, `:813` is a substring of the class
  name `TestRecoverPictureResults`. `MagicMock` stays (13 other uses).
- `doc_store_path` — no Python reader anywhere; the only other hits are two aspirational
  plan docs under `docs/superpowers/plans/`.

## 3. NOT applied — `PictureRegion` (contested)

`src/pageindex_mcp/picture_plane.py:119` `PictureRegion` (+ `has_content`,
`is_landscape_fallback`) was **deliberately left in place**, because the same workflow
run produced two opposing skeptic verdicts on it:

- **Round-1 skeptic — REFUTED.** `audit/REMEDIATION_PLAN_2026-08-24.md:183` is a Zone-4
  "Code targets" row naming lines 115–138 (exactly this symbol) with the constraint
  "**`PictureRegion`, `RegionClassification`, `_classify_region` unchanged**". That plan
  is unexecuted (`grep -rn RegionMetadata src/` → zero) and its Zone 4 is
  `not_implemented`, so the constraint is live-but-stale, not retired.
- **Cascade-round skeptic — NOT REFUTED.** Reads the same row as a non-goal constraint on
  a `RegionMetadata` dataclass that was never built, therefore not a retention order.

Both agree the symbol is **runtime-dead** (zero LSP references, zero non-`audit/` greps).
The disagreement is purely governance. Resolution requires a human decision: formally
retire or supersede REMEDIATION_PLAN_2026-08-24 Zone 4, then delete in a follow-up.

The cascade skeptic also flagged a **line-range error** that would have broken the build:
the verifier's span `118-145` is off by one at both ends; the correct span is `114-147`
(banner 114–117, blank 118, decorator 119, class body 120–146). Deleting 118–145 would
have orphaned line 146 → `IndentationError`. Recorded here so the follow-up uses 114–147.

## 4. Other refuted / alive items (do not re-litigate)

| Symbol | Verdict | Reason |
|---|---|---|
| `client/indexer.py:239 _IMAGE_STANDALONE_PIPELINE_ENABLED` | REFUTED | Deliberate retention recorded one day earlier in `DEAD_CODE_AUDIT_2026-09-05.md:72-75`; also a documented wiring-check row in `REMEDIATION_PLAN_2026-08-25.md:361` whose check type is literally "import". Reversing needs a governance amendment, not a second verifier opinion. |
| `HeuristicRegistry.list_expired` | ALIVE | Live caller at `tests/test_heuristic_registry.py:69`. |
| `helpers/types.py reset_verdict_thresholds` | ALIVE | Four live references incl. `helpers/__init__.py:56`. |

## 5. Verification of this pass

- `ruff check` on the five touched files, diffed against a `git worktree` at HEAD:
  **0 new findings, 1 fixed** (the F811 duplicate import). The 51 pre-existing findings
  in these files are unchanged and predate this pass — note they contradict the
  `ruff_violations: 0` policy documented in `pyproject.toml`, which is a separate
  standing issue.
- Import smoke test: `pageindex_mcp.config`, `picture_plane`, `helpers.heuristic_registry`
  all import cleanly.
- `uv run pytest`: **1965 passed, 8 skipped, 2 xfailed, 1 xpassed, 0 failed** (258s).
- Net: **71 lines deleted** across 5 files.

## 6. Coverage gaps carried forward

The completeness critic recorded these as **not closed** by this run:

1. **ruff never ran over `tests/`** — the static lens scoped itself to `src scripts services *.py`.
   `ruff check --select F401,F811,F841,F842 tests` reports ~60 findings that no lens saw.
2. **~480 zero-degree `Variable` nodes** in the graph were never individually verified; the
   lens self-reported a 4-of-4 false-positive rate on its spot-checks.
3. **~108 of 120 functions carrying a TESTS-only edge** were never traced; the lens's Cypher
   did not execute.
4. **`query_graph` relationship patterns are broken for this project** — `IMPORTS`, `DEFINES`,
   `SIMILAR_TO` all return 0 rows despite the schema reporting 678/10182/76 such edges.
   Worth fixing before the next run; it blinded the modules lens.
5. **lsp-unreachable lens produced zero signal by construction** — pyright runs in basic mode
   without `reportUnusedImport`/`reportUnusedVariable`.
6. **config-flags lens step 3** (flags documented as permanently on/off with a dead else-branch)
   not done.
7. **`agents/governance/known-advisories.yaml` and `scope-checklist.yaml` were never read.**
8. **CLAUDE.md Hard Rule 6 was not satisfied** — no `/mem-search` was issued against either
   claude-mem project by any lens. Prior dead-code decisions in memory were invisible to
   this run.

Closed with negatives (do not re-run): `services/` (8 nodes, all protected entry points),
`scripts/` functions (all have live in-file callers), config-flags step 4 (41 env vars
diffed; the 19 without a Python reader are all documentation-only).
