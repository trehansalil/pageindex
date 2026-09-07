# Dead-Code Audit — 2026-09-07 (Run 6)

- **Workflow:** `dead-code-discover-verify` (task `wm76kuwhk`, run `wf_44ebb2d7-91b`)
- **Base HEAD:** `426216c` · **Branch:** `ICR-97-rfc44-recovery-dispatch-wiring` · tree clean at dispatch
- **Phases:** Discover (7 lenses) → Verify (9 zones) → Refute (adversarial skeptic) → Critique (+ cascade round)
- **Cost:** 30 agents · 0 errors · 982 tool calls · 3.44M subagent tokens · ~103 min wall clock
- **Supersedes:** nothing. Run 4 and Run 5 shipped removals (`2094a07`, `426216c`) without writing a report; this run re-baselines against the post-Run-5 tree.

> **Nothing in this run is recommended for removal as-is.** All 60 "confirmed"
> items land in exactly the two tiers `DEAD_CODE_AUDIT_2026-09-05.md` §4
> deliberately deferred, and the run carries two integrity defects (§2). Read
> §2 before acting on any table below.

---

## 1. Result summary

| Metric | Value |
|---|---|
| Lenses completed | 7 / 7 (`find:lsp-unreachable` stalled once, retried, completed) |
| Unique candidates | 65 |
| Verification groups | 9 zones (core 4, helpers 6, converters 10, scripts 16, client 1, worker 5, storage 1, registry 3, tests 18) |
| Confirmed dead | 60 (27 verify round + 33 cascade round) |
| Refuted by skeptic | 23 |
| Alive | 14 |
| Unsure | 0 |
| Protected entries excluded up front | 108 |

**Composition of the 60 confirmed — this is the finding:**

| Bucket | N | Prior disposition |
|---|---|---|
| Package-facade (`__init__.py`) re-exports + `__all__` entries | 45 | **Deferred** by `DEAD_CODE_AUDIT_2026-09-05.md` §4 as "Medium risk. These are public API surface." |
| `tests/` constants and private helpers | 15 | **Deferred** by the same §4 pending RFC-044 Wave 7 (test-suite reduction), which is still unstarted |
| Anything else — production functions, classes, modules, branches | **0** | — |

Zero production symbols were found dead. The graph lens's 63 zero-degree
Function/Method/Class nodes were all grep-refuted as indexer artefacts
(dict/kwarg registry dispatch, string-keyed recovery functions, relative and
function-local imports the IMPORTS extractor misses).

---

## 2. Run integrity — read before trusting the tables

### 2.1 The executed script carried a stale dispatch context (environment drift)

`Workflow({name: …})` resolved from a registry snapshot taken at session start,
so pre-launch edits to `.claude/workflows/dead-code-discover-verify.js` did not
reach the agents. Every lens was told:

- repo root `/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex` — does not exist here
- codebase-memory project `Users-saliltrehan-Documents-Python_n_R-Personal-pageindex` — not in `list_projects`
- base `HEAD 9e2b310` — actually `426216c`
- "working tree has intentional uncommitted changes (5 test files, 1 tasks note)" — tree was clean

All seven lenses detected the drift independently and substituted the real
project (`mnt-HC_Volume_106759881-pageindex_deployment`, 10,743 nodes / 22,400
edges, re-indexed at `426216c` immediately before launch). Results are on the
correct repo, but the recovery cost tool budget and the critic flags it as a
systemic gap. **Fix for the next run:** pass `scriptPath` explicitly rather than
`name`, or restart the session after editing the workflow. The project copy of
the script has since been corrected.

### 2.2 The cascade round re-confirmed six items its own skeptic had refuted

The workflow de-duplicates cascade candidates against already-adjudicated ones
with the key `` `${file}::${name}` ``. Cascade agents labelled the same symbols
`X (facade re-export)` where the verify round had used `X` or
`X (re-export from worker package facade)`, so the key missed and six refuted
items re-entered the confirmed set through a second, weaker adjudication:

| Symbol | File | Verify round | Cascade round |
|---|---|---|---|
| `MAX_JOBS_DEFAULT` | `worker/__init__.py:31` | **refuted** (barrel contract) | confirmed |
| `_LLM_TERMINAL_INDICATORS` | `worker/__init__.py:15` | **refuted** (barrel contract) | confirmed |
| `_VERDICT_RETRY_TTL_S` | `worker/__init__.py:40` | **refuted** (twin `_VERDICT_RETRY_KEY_PREFIX` left behind → split API tier) | confirmed |
| `_mirror_bridged_set` | `worker/__init__.py:43` | **refuted** (unexplained graph edge from `_mirror_bridged_incr`) | confirmed |
| `_AR_LETTER_RE` | `converters/__init__.py:9` | **refuted** (half of a compat alias pair; `_AR_SCRIPT_RE` is live) | confirmed |
| `_RFC029_TABLE_DEDUP_ENABLED` | `converters/__init__.py:26` | **refuted** (feature-flag pair; `_RFC029_TABLE_MIN_COLLAPSE_COLS` left behind) | confirmed |

The refutations are the stronger verdicts — each skeptic *conceded* the code
evidence ("CODE LENS AGREES, GOVERNANCE LENS REFUTES") and refuted on grounds
the cascade agents never examined. **Treat all six as refuted.** The remaining
33 cascade confirmations were produced by the same agents applying the same
code-only criterion to the same class of symbol, so they inherit the same doubt.

### 2.3 Line-number encoding is inconsistent between rounds

- **converters items** encode `start_line` = the `from .x import` line and
  `end_line` = the matching `__all__` string — e.g. `_VERDICT_RANK 64 + 194`.
  These are **two separate single lines, not a range.** Deleting lines 64–194
  would remove ~130 lines of live facade.
- **helpers / registry_backfill items** point only at the import line and file
  the `__all__` entry as a separate confirmed item (§3.1, §3.4).

---

## 3. Confirmed set (for the record — not cleared for removal)

Every item below is a package-facade re-export or a `tests/` symbol. The four
`__init__.py` files each declare themselves a backward-compatibility barrel in
their own docstring, and `agents/governance/vocabulary.yaml:62-65` codifies the
role (`barrel: role: "re-exports only"`). A barrel entry with zero in-repo
consumers is in its **designed** state, not a dead one — which is exactly why
the 2026-09-05 audit deferred this tier. Removing entries is a governance
decision about the public API surface, not a mechanical cleanup.

### 3.1–3.4 Package facades

### helpers/__init__.py
| Symbol | Line(s) | Risk | Round |
|---|---|---|---|
| `_count_empty_body_nodes` | 192 | medium | verify |
| `_flat_is_separator_row` | 157 | medium | verify |
| `_flat_split_pipe_row` | 159 | medium | verify |
| `"_count_empty_body_nodes"` (`__all__` entry) | 258 | low | cascade |
| `"_flat_is_separator_row"` (`__all__` entry) | 266 | low | cascade |
| `"_flat_split_pipe_row"` (`__all__` entry) | 270 | low | cascade |
| `ExtractionSnapshot` | 60 | medium | cascade |
| `FEATURE_WIRINGS` | 109 | medium | cascade |
| `FeatureWiring` | 114 | medium | cascade |
| `_UNSET` | 40 | low | cascade |
| `_GateFn` | 51 | low | cascade |
| `_flat_is_pipe_row` | 156 | low | cascade |
| `_flat_verbalize_rows` | 160 | low | cascade |
| `_looks_like_toc_page` | 146 | low | cascade |
| `_walk_leaves` | 200 | low | cascade |

### converters/__init__.py
| Symbol | Line(s) | Risk | Round |
|---|---|---|---|
| `_VERDICT_RANK` | 64 + 194 | medium | verify |
| `StageRecord` | 162 + 207 | medium | verify |
| `_collect_heading_pages` | 68 + 222 | medium | verify |
| `_docling_chunk_worker` | 30 + 227 | medium | verify |
| `_figure_desc_inline` | 130 + 231 | medium | verify |
| `_md_to_structure` | 77 + 244 | medium | verify |
| `_run_docling_chunk_with_timeout` | 36 + 263 | medium | verify |
| `_AR_LETTER_RE` | 9 | low | cascade **⚠ refuted in main round** |
| `_AR_MARKER_CAPTURE_RE` | 60 | low | cascade |
| `_AR_SCRIPT_RE` | 10 | low | cascade |
| `_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S` | 25 | low | cascade |
| `_IMAGE_ENRICH_CONCURRENCY` | 115 | low | cascade |
| `_RFC029_TABLE_DEDUP_ENABLED` | 26 | low | cascade **⚠ refuted in main round** |
| `_crop_page_region` | 128 | low | cascade |
| `_pdf_to_markdown_docling_chunked` | 34 | low | cascade |
| `_split_run_together_headings` | 97 | low | cascade |

### worker/__init__.py
| Symbol | Line(s) | Risk | Round |
|---|---|---|---|
| `KILL_GRACE_SECONDS` | 49 | medium | cascade |
| `MAX_JOBS_DEFAULT` | 31 | medium | cascade **⚠ refuted in main round** |
| `_CHILD_ERROR_REGISTRY` | 13 | medium | cascade |
| `_DEFAULT_CHILD_CLASSIFICATION` | 14 | medium | cascade |
| `_LLM_TERMINAL_INDICATORS` | 15 | medium | cascade **⚠ refuted in main round** |
| `_TERMINAL_CHILD_REASONS` | 16 | medium | cascade |
| `_VERDICT_RETRY_TTL_S` | 40 | medium | cascade **⚠ refuted in main round** |
| `ChildErrorClassification` | 17 | medium | cascade |
| `_mirror_bridged_set` | 43 | medium | cascade **⚠ refuted in main round** |

### registry_backfill/__init__.py
| Symbol | Line(s) | Risk | Round |
|---|---|---|---|
| `_preflight_checks` | 71 | low | verify |
| `_prepare_metas` | 72 | low | verify |
| `"_preflight_checks"` (`__all__` entry) | 100 | low | cascade |
| `"_prepare_metas"` (`__all__` entry) | 101 | low | cascade |
| `_record_reconcile_heartbeat` | 85 | low | cascade |

### tests/
| Symbol | File | Line(s) | Kind |
|---|---|---|---|
| `_REVERSED_ARABIC` | `tests/test_bidi.py` | 99–99 | variable |
| `_ZERO_SCORE_TEXT` | `tests/test_bidi.py` | 751–751 | variable |
| `_fake_settings_rfc_bidi` | `tests/test_bidi.py` | 971–985 | test_helper |
| `_CORPUS_MD_FILES` | `tests/test_bidi.py` | 1009–1009 | variable |
| `_VISUAL_LINE_AGPL` | `tests/test_bidi.py` | 1013–1013 | variable |
| `_VISUAL_LINE_2_AGPL` | `tests/test_bidi.py` | 1014–1014 | variable |
| `_REVERSED_WORD` | `tests/test_bidi.py` | 1015–1015 | variable |
| `_LOGICAL_LINE_AGPL` | `tests/test_bidi.py` | 1018–1018 | variable |
| `_CLEAN_LINE_2` | `tests/test_bidi.py` | 1019–1019 | variable |
| `_ARABIC_SHAPING_RANGES` | `tests/test_bidi.py` | 1021–1021 | variable |
| `_NUMERIC_JUNK_FLAT_MD` | `tests/test_bidi.py` | 1686–1686 | variable |
| `_LOGICAL_BODY_LINE` | `tests/test_converters.py` | 142–144 | variable |
| `_VISUAL_HEADING` | `tests/test_converters.py` | 2220–2220 | variable |
| `_healthy_leaf` | `tests/test_verdict.py` | 2185–2186 | test_helper |
| `_varied_text_rfc030` | `tests/test_verdict.py` | 2192–2195 | test_helper |

---

## 4. Refuted / alive — do not re-litigate

**Refuted (23).** Beyond the six in §2.2:

- `REGISTRY_METRICS_SYNC_INTERVAL_S`, `_BRIDGE_REDIS_PREFIX`, `CONTENT_TYPE` (`metrics/__init__.py`) — deliberate re-exports; `metrics` is the one package with a real star-import consumer (`docs/superpowers/plans/2026-04-07-grafana-monitoring.md:1406`).
- `RECONCILE_ETAG_KEY` (`storage/__init__.py`) — deliberate re-export.
- `arabic_ratio` (`script.py`) — low-confidence refutation on governance state; the skeptic could not break the liveness case and said so.
- `ACYCLIC`, `NODES_RESOLVE`, `EXEC_ORDER`, `DERIVED_CONSISTENT` (`scripts/gates/dag.sh`), `UPLOAD_TO_QUERY` (`scripts/gates/e2e.sh`) — mechanically unexpanded shell vars, retained as gate declarations.
- `MAX_CYCLOMATIC`, `MAX_FUNCTION_LINES`, `MAX_FILE_LINES`, `MAX_NESTING`, `MAX_PARAMS` (`scripts/gates/static.sh`) — `pyproject.toml:114-123` states verbatim that these are governance thresholds mapped to ruff rules. Explicit retention.
- `upsert_verdict` (`registry/queries.py`) — inside a deprecation window; removal is sequenced by governance.
- `st_garble_config`, `st_blob_kind`, `st_tree_gate_result` (`tests/test_triad_properties.py`) — Hypothesis strategies held by contract.

**Alive (14).** `BULK_PROFILE` (~30 references), `HeuristicRegistry.get` (7 call sites), `reset_verdict_thresholds` (already adjudicated 2026-09-06), `PictureRegion` (protected — and still carrying the unresolved Zone-4 governance question from Run 3), `WRITE_BARRIER_EXHAUSTED` (docstring documents deliberate retention for `/metrics` stability), three ERA001 comment blocks, and the CLI scripts `ingest_via_server.py`, `stress_test.py`, `test.py`, `scripts/minio_helper.py`, `scripts/prebake_tessdata.sh`, `hash_cache_migrate.py` (protected).

`scripts/table_separator_baseline.py` came back **unsure** from the cascade skeptic: no in-repo invoker, but an active runbook instruction references it.

---

## 5. Coverage gaps carried forward

The completeness critic returned 14 gaps. The ones that change what a future run should do:

1. **`lsp-unreachable` is a null instrument.** No `pyrightconfig.json` exists, so Pyright runs in basic mode where `reportUnusedImport` / `reportUnusedVariable` / `reportUnusedClass` / `reportUnusedFunction` are all disabled — the diagnostics the lens greps for can never be emitted. The Serena venv also lacks project dependencies. Its zero result carries no information. **Fix: add a `pyrightconfig.json` with those four rules at `warning`, or drop the lens.**
2. **57 ARG001/ARG002 unused-argument sites counted but never enumerated — third consecutive run.** The lens is instructed to report counts only.
3. **Graph `IMPORTS`-edge extraction is unsound.** Misses `from . import X`, parenthesized multi-line imports, and function-local imports; `STARTS WITH` and `NOT EXISTS` both silently return 0 in this Cypher engine; node start/end lines disagree with the file. Four whole-module false positives were caught this way. Module-level conclusions need grep corroboration.
4. **673 of 683 zero-degree `Variable` nodes were cleared by an unpreserved bulk heuristic** (whole-word regex count), not by verification. Not reproducible from any artefact.
5. **115 `Settings`/`PipelineConfig` fields cleared with grep-class evidence**; Serena `find_referencing_symbols` was run for only 2. A field whose sole hit is a docstring or `.md` mention reads as live.
6. **Modules-files lens under-recalled its own headline check.** It claims all 412 `__all__` names across 9 packages were swept; the critic's scripted re-run found 39 own-package-only facade exports where the lens reported 12 — including 4 in `metrics/`, a package it listed as swept but reported nothing from.
7. **`services/` never checked at Class/Variable level** (Function level is clean: 5 zero-degree nodes, all protected FastAPI routes/`Depends` callables). **`scripts/gates/*.sh` never degree-queried at all.** `scripts/spikes` does not exist — the dispatch question is unanswerable as posed; the real surface is `agents/spikes/` plus two one-shot probes.
8. **~124 `tests/` module-level constants with `in_degree=0` and ≥2 in-file grep hits were treated as live without Serena re-verification.** A constant referenced only by an already-dead helper in the same file survives that filter — and this run confirmed 4 dead test helpers, so the pattern exists.

---

## 6. Recommendation

**Do not ship a removal commit from this run.** Two things are worth doing instead:

1. **Settle the barrel question once, as governance.** Forty-five of the 60 confirmed items — and the entire deferred tier from 2026-09-05 — are the same question: are `helpers`, `converters`, `worker`, `registry_backfill`, `metrics`, `storage` `__init__.py` files a supported public API, or an artefact of the monolith split that should shrink to what the repo actually imports? Answer that in an RFC and the tier resolves in one pass instead of being re-discovered and re-refuted every run. Until then the lens will keep producing this same list.
2. **Fix the two instrument defects before Run 7** — `pyrightconfig.json` for the LSP lens (§5.1), and the cascade de-duplication key (§2.2), which should normalise the parenthetical suffix or key on `file:start_line` instead of `file::name`.

The `tests/` bucket stays deferred to RFC-044 Wave 7, per the standing decision.

---

*Generated from `dead-code-discover-verify` run `wf_44ebb2d7-91b`. Raw structured output: 30 agent results in `journal.jsonl` under the session's `subagents/workflows/` directory.*
