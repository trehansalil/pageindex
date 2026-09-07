# Dead-Code Audit — 2026-09-05

**Run:** `dead-code-discover-verify` workflow (task `whha2zjxx`, run `wf_52026ba9-e68`)
**Base:** HEAD `9e2b310`, branch `ICR-97-rfc44-recovery-dispatch-wiring`, tree clean at dispatch
**Cost:** 28 agents · 0 errors · 622 tool calls · 2,464,603 subagent tokens · ~68 min
**Phases:** Discover (7 lenses) → Verify (9 zones) → Refute (adversarial skeptic) → Critique (completeness + cascade)

> **Read this run as partial.** Two of its three mandated tool backends were unavailable
> during discovery. See [Run integrity](#run-integrity). A re-run is owed.

---

## 1. Headline

| Metric | Value |
|---|---|
| Lenses reporting | 7 (1 of them 100% blocked, see §5) |
| Unique candidates | 74 |
| Verification groups | 9 |
| **Confirmed dead** | **63 rows → 52 unique `file:line` → 41 genuinely distinct** |
| Refuted by skeptic | 10 |
| Ruled alive | 10 |
| Unsure | 0 |
| Protected (never remove) | 13 |
| Cascade findings | 3 (only 1 genuine) |
| Critic-identified gaps | 14 |
| Risk split | 56 low · 7 medium · 0 high |

The 63 → 41 collapse is a report-hygiene defect in the run itself: every unused import is
listed twice (e.g. `FLAT_MARKDOWN_PROFILE` and `FLAT_MARKDOWN_PROFILE (import)` are both
`client/indexer.py:51`). A mechanical executor would attempt each edit twice.

---

## 2. Confirmed dead, by zone

| Zone | N | Content |
|---|---|---|
| tests | 25 | 24 unreferenced private test helpers across 11 files |
| helpers | 18 | unused imports; `HeuristicRegistry.all_entries`; `_get_verdict_thresholds`; dead `infer_script` alias; zero-external `__all__` re-exports |
| client | 8 | 4 unused imports (`indexer.py:51/54/58`, `recovery.py:30`) |
| scripts | 4 | 4 whole dead files |
| core | 3 | `script.py:26 LOGICAL_RANGES`, `script.py:132 arabic_letter_ratio`, `metrics/__init__` re-exports |
| worker | 2 | private + public `__all__` re-exports |
| converters | 2 | `picture_plane.py:119 PictureRegion`, `picture_plane.py:98 SkipReason.counts_in_enrichment_denominator` |
| storage | 1 | `storage/verdict.py:37 _META_FIELDS` |

By kind: 24 test_helper · 22 import · 5 export · 4 file · 2 method · 2 function · 2 constant · 1 variable · 1 class.

---

## 3. Applied in this pass (low-risk tier)

Both groups were ruff-verified at HEAD and re-verified after the edit.

### 3.1 Unused imports removed — 11 sites

| File | Line | Symbol |
|---|---|---|
| `src/pageindex_mcp/client/indexer.py` | 51 | `FLAT_MARKDOWN_PROFILE` |
| `src/pageindex_mcp/client/indexer.py` | 54 | `GarbleReport` |
| `src/pageindex_mcp/client/indexer.py` | 58 | `TreeGateResult` |
| `src/pageindex_mcp/client/recovery.py` | 30 | `TreeGateResult` |
| `src/pageindex_mcp/helpers/garble.py` | 6 | `os` |
| `src/pageindex_mcp/helpers/gates.py` | 9 | `_infer_script` |
| `src/pageindex_mcp/helpers/types.py` | 8 | `field` (dataclasses) |
| `src/pageindex_mcp/helpers/verdict.py` | 5 | `logging` |
| `src/pageindex_mcp/helpers/verdict.py` | 9 | `decide_rtl` |
| `src/pageindex_mcp/helpers/verdict.py` | 12 | `BULK_PROFILE` |
| `src/pageindex_mcp/helpers/verdict.py` | 14 | `GarbleConfig` |

**Deliberately NOT removed:** `client/indexer.py` `_IMAGE_STANDALONE_PIPELINE_ENABLED`
(F401-flagged but **refuted** — it is a monkeypatch target; the live uses are in
`client/images.py:102`). `ruff --fix` would have deleted it. This is exactly why the
skeptic phase exists, and why blanket `ruff --fix` is the wrong tool here.

### 3.2 Dead files deleted — 4

- `analyze_doc.py` (129 lines) — zero repo-wide references
- `scripts/spikes/extract_docling.py` (67)
- `scripts/spikes/extract_docling_cpu.py` (78)
- `scripts/spikes/extract_pymupdf4llm.py` (43)

The three spikes referenced only themselves (in their own `Run with:` docstrings) and a
`/tmp/docling_venv` interpreter that no longer exists. Removing all three empties
`scripts/spikes/`, so the directory goes with them.

### 3.3 Verification

| Check | Before | After |
|---|---|---|
| `ruff check src tests scripts services *.py` | 443 | **422** (−21, no new codes) |
| `ruff --select F401,F811,F841,F842` | 12 | **1** (the refuted one, intentionally kept) |
| `pytest` (full suite) | — | **2056 passed**, 8 skipped, 2 xfailed, 1 xpassed, 0 failed (270s) |

### 3.4 Cascade consequence

`BULK_PROFILE` (`helpers/garble.py:460`) is the **only genuine cascade in the entire
confirmed set**. Removing the `helpers/verdict.py:12` import took its last production
reader; it is now consumed exclusively by 8 test modules through the
`tests/_garble_compat.py` `check_garble` shim. It is **not** deleted — it is now
test-only, which is a decision to make deliberately, not a side effect.

Every other import's source symbol keeps other `src/` consumers, verified site-by-site:
`FLAT_MARKDOWN_PROFILE` → `client/images.py:113`; `GarbleReport` → `garble.py:610,813`;
`TreeGateResult` → `tree_validation.py:441,470`, `verdict.py:128,149`; `GarbleConfig` →
`garble.py:345,485,498`; `decide_rtl` → `recovery.py:605`, `normalize.py:118,128`,
`headings.py:141`, `tree_validation.py:423`; `_infer_script` → `garble.py:575,706,718`,
`tree_validation.py:295,413`.

---

## 4. Deferred (NOT applied)

| Tier | N | Why deferred |
|---|---|---|
| Unused private test helpers | 24 | Low individual risk, but they overlap RFC-044 Wave 7 (test-suite reduction ~2050 → ~1000), which is unstarted and unmeasured. Delete them as part of that wave, not ahead of it. The critic also notes the tests-lens deferred a **sibling cluster of 6 dead constants** in `tests/test_bidi.py` (`_CORPUS_MD_FILES`, `_REVERSED_WORD`, `_CLEAN_LINE_2`, `_VISUAL_LINE_AGPL`, `_VISUAL_LINE_2_AGPL`, `_LOGICAL_LINE_AGPL`) that share the fate of the dead `_nfkc`/`_patch_fitz` block but are absent from the confirmed set. |
| `__all__` / facade re-exports | 5 | Medium risk. These are public API surface (`helpers`, `metrics`, `worker`, `converters`, `storage`). "Zero referencer outside the defining package" was established by identifier-token sweep, which the critic itself flags as unsound for names that collide across modules — that is precisely how `infer_script` escaped detection and had to be caught by hand. |
| `src/` symbols (`PictureRegion`, `arabic_letter_ratio`, `LOGICAL_RANGES`, `_META_FIELDS`, `HeuristicRegistry.all_entries`, `_get_verdict_thresholds`, `SkipReason.counts_in_enrichment_denominator`) | 8 | Found by the **critic**, not by any lens — they are the substitute-sweep output for the blocked graph lens, and have had no independent second opinion. Each is Serena-confirmed zero-reference, but they deserve the refutation pass the other candidates got. |
| Duplicate implementation: `script.infer_script` vs the dead `garble.py:638` alias that `helpers/__init__` re-exports | 1 | Real finding, but the systematic near-duplicate sweep (`query_graph` SIMILAR_TO / jaccard across 69 `src/` modules) was never run — fix the class, not the one instance. |

---

## 5. Run integrity — read before trusting the numbers

The run's own critic is explicit about what it could not see.

### Blocked: graph-degree lens — **100%**

> "The codebase-memory MCP tools (`search_graph`, `query_graph`, `trace_path`,
> `get_code_snippet`, `search_code`) are not present in this session's tool list at all."

Cause: `codebase-memory-mcp` is registered in `~/.claude.json` under project key
`/root/pageindex_deployment`, but this session's cwd is
`/mnt/HC_Volume_106759881/pageindex_deployment`, whose `mcpServers` is `{}`. To its
credit the agent **refused to fabricate candidates from grep** and reported the blockage.

The critic substituted an AST + whole-repo identifier-index sweep (118 `.py` files) and
that substitute alone produced **8 zero-reference `src/` symbols that none of the seven
lenses found**. That is the measured cost of the missing lens.

### Blocked: Serena/LSP — every discovery lens

> "Serena/LSP was down for every lens (CONNECTION_CLOSED) but is **REACHABLE NOW**."

`lsp-unreachable` fell back to ruff. Still entirely uncovered, because no ruff rule exists
for them: Pyright hint-severity diagnostics (`reportUnusedImport`, `reportUnusedVariable`,
`reportUnusedExpression`, "is not accessed"), unreachable-code-after-return/raise, and
"self is not accessed". Serena came back only in time for the critic phase.

### Stale dispatch context

The script pinned `REPO = /Users/saliltrehan/Documents/Python_n_R/Personal/pageindex`
(does not exist in this sandbox) and asserted "the working tree has intentional
uncommitted changes (5 test files, 1 tasks note)" — false; the tree was clean at
`9e2b310`. Any finding whose evidence depended on those files could not be checked.

### Remaining uncovered surface

- 57 `ARG001`/`ARG002` sites reported as bare statistics, zero triage
- `services/docling-service`, `services/docling-ocr-service`, `services/paddleocr-service` — three separate FastAPI deployables, basename-grep coverage only, no symbol-level analysis
- `scripts/gates/` and `scripts/lib/` never enumerated by any lens
- RFC-044 Wave 7 test reduction — the largest known body of dead weight, entirely un-enumerated
- `config-flags` lens could not reach Obsidian MCP, so no RFC/design note was checked for blessing removal of the 6 dead module-level config aliases
- `audit/zones/ocr-pipeline-decision-recovery-cascade.md` is stale against HEAD (its "four OCR recovery methods = zero production callers" finding was resolved by RFC-043/044) and its frontmatter status was never updated — any future lens re-reading it will re-derive already-fixed findings

**Critic's stated highest-value next action:** re-run the `lsp-unreachable` lens now that
Serena works.

---

## 6. Protected — never propose for removal

13 items. Removing any of these is a correctness or compliance regression, not a cleanup.

| Item | File | Why |
|---|---|---|
| `PII_CORPUS` / `pii_corpus` / `require_zdr_compliance` / `validate_hr3_compliance` | `config.py` | **CLAUDE.md Hard Rule 3** — ZDR routing boot-gate. Low call-site count is not deadness. |
| `cleanup_protect_empty_processed_at` | `config.py` | **CLAUDE.md Hard Rule 2** — erasure cascade / dual-write consistency. Live readers in `registry_backfill/cleanup.py`. |
| `TreeDefect.ARABIC_LOW_CONTENT_RATIO` | `helpers/types.py:34` | Enum member kept for persisted `verdict_reason` compatibility; comment says so. |
| `OCR_ESCALATION_LOW_CONTENT` | `config.py` | Alias name has no readers, but `pipeline_config.ocr_escalation_low_content` is read live at `client/recovery.py:456`. |
| `MAX_JOBS_CEILING` / `PAGEINDEX_WORKER_MAX_JOBS` | `worker/lifecycle.py:48` | Live `os.getenv`, re-exported and tested. |
| `registry_backfill/__main__.py` | — | `python -m` entry point, documented at `README.md:462-463`. Executed, never imported. |
| `hash_cache_migrate.py` | — | Documented manual migration CLI, `README.md:487`. |
| `decide_ocr_strategy(document_type=...)` | `picture_plane.py` | RFC-044 D4 explicitly retains it as the Zone-8 typed contract. |
| `DocumentType` | `picture_plane.py` | Same — RFC-044 design line 342 / tasks 3.2. |
| OCR recovery dispatch methods (`_recover_garble_ocr`, `_recover_low_content_ocr`, `_recover_rtl_repair`, `_recover_rtl_flat_compare`, `_recover_vlm_fallback`) | `client/recovery.py` | The zone note claiming they are unreferenced is stale; RFC-043/044 wired them. |
| `test_decomposed_verdict_is_unreferenced` | `tests/test_architecture_guards.py` | The active regression guard documenting `_decomposed_verdict`'s dead status. Must outlive its subject. |
| autouse pytest fixtures (`_instant_memory_gate`, `_reset_verdict_thresholds_cache`, `_reset_auth_warned`, `_restore_config_module_identity`, `_restore_pipeline_config`, `_reset_remote_docling_version_cache`, `_reset_primary_zdr_verified_cache`, `_reset_module_caches`, `_reset_guards`, `_restore_module_state`, …) | `tests/` | Never referenced by name — that is how autouse works. |
| `assert not hasattr(...)` architecture-guard sites (`apply_verdict_hysteresis`, `flat_applicable`, `_verdict_cas_guard`, `_VERDICT_CAS_FIELDS`, `garble_prongs`, `_save_doc_meta`, `decide_ocr_mode`, `FLAT_GATE_SUBSET`, `_FLAT_APPLICABLE_DEFECTS`, `_flat_block_text`, `read_verdict_ledger`, `persist_verdict_ledger`) | `tests/test_architecture_guards.py` | Named only inside negative assertions. Grep reads them as dead; they are the guard. |

---

## 7. Notable saves by the skeptic

10 candidates refuted, 10 ruled alive. The pattern worth remembering: the `config.py`
backward-compat aliases (`OCR_ESCALATION_GARBLE`, `PDF_INSPECTOR_PRECLASSIFY`,
`REMOTE_MD_RENORMALIZE`, `ALLOW_AGPL_FALLBACK`, `OCR_ESCALATION_PER_PICTURE`,
`IMAGE_DOMINANT_OCR_ESCALATION_ENABLED`) all look dead to a `grep import <NAME>` but are
consumed by **attribute** reference on the config module. A finder found the live import
for `OCR_ESCALATION_GARBLE` at `tests/test_converters.py:23` and discounted it.

Four "commented-out code" candidates turned out to be **prose comments**, not code —
`indexer.py:1494-1499`, `memory_admission.py:41`, `metrics/definitions.py:199-207`.

`_remote_image_to_markdown` (`client/remote.py:160`) has zero production callers and only
test callers, but is flagged **alive, low-confidence** as a probable **ZDR-egress wiring
defect** — i.e. a bug to fix, not code to delete. Worth a separate look under Hard Rule 3.

---

## 8. Next actions

1. ~~**Register `codebase-memory-mcp` + `obsidian`**~~ — **DONE.** Both are now registered
   under `/mnt/HC_Volume_106759881/pageindex_deployment`. Note the server was *also*
   holding an **empty graph** (`list_projects` -> `[]`), so connecting it was necessary but
   not sufficient; the repo has since been indexed
   (`mnt-HC_Volume_106759881-pageindex_deployment`, **10,868 nodes / 22,013 edges**, full
   mode). Serena is activated on the same path with python/bash/markdown/yaml servers.
2. ~~**Fix `.claude/workflows/dead-code-discover-verify.js`**~~ — **DONE** (applied via
   `audit/dead-code-workflow-fixes.patch`). `PROJECT`/`REPO` are now env-overridable and
   default to the real mount; the pinned HEAD is replaced by a freshness check; Hard Rule 6
   is stated explicitly; the false uncommitted-tree premise is replaced by
   "run `git status --short` yourself". Note `.claude/` is NOT git-tracked, so this change
   lives only on disk and will be overwritten by the next `make sync-claude` from the dev
   machine unless the same edit is made there.
3. **Add `--chmod=F644` to the `sync-claude` Makefile target** (line 188). `rsync -a`
   preserves the 0600/uid-501 modes from the source machine, which is the root cause of
   (2) and of three workflow files being unreadable earlier in this session.
4. **Re-run the workflow** once (1) and (2) land. Note that a bare `resumeFromRunId` will
   replay the blocked lenses from cache — their prompts must change (which fixing
   `PROJECT`/`REPO` accomplishes) to force re-execution. ~24 of 28 agents replay free.
5. Triage the 57 `ARG001`/`ARG002` sites; run the near-duplicate sweep; enumerate
   `scripts/gates/`, `scripts/lib/`, and the three `services/` apps.
6. Update the stale frontmatter on `audit/zones/ocr-pipeline-decision-recovery-cascade.md`.

---

*Raw workflow output: 389 KB JSON at `/tmp/claude-0/.../tasks/whha2zjxx.output`.
Journal: `.../subagents/workflows/wf_52026ba9-e68/journal.jsonl`.*

---

## 9. Run 2 — all seven lenses live (run `wf_0adb25f3-396`)

Re-run after fixing the two dark lenses (codebase-memory MCP registered + repo indexed at
10,868 nodes / 22,013 edges; serena activated on the correct path) and correcting the
workflow script's hardcoded `PROJECT`/`REPO` constants and its false "working tree is clean"
premise. 31 agents, 0 errors, 775 tool calls, 2,755,097 subagent tokens, 68 min.

| | Run 1 (5/7 lenses) | Run 2 (7/7 lenses) |
|---|---|---|
| Lenses OK | 5 | **7** |
| Unique candidates | — | 79 |
| Confirmed | 63 | 35 |
| Refuted | 10 | **30** |
| Unsure | 0 | 1 |
| Alive | 10 | 15 |
| Protected | 13 | 54 |

Confirmed fell 63 → 35 mostly because §3's 11 imports and 4 files are already gone from the
working tree. The real signal is **refuted tripling to 30**: the newly-lit lenses mostly
generated candidates that the skeptics then killed.

### 9.1 What the two previously-dark lenses actually bought

Lens attribution across the 35 confirmed: `tests-lens` 26, `docs-history` 5,
`graph-degree` 2, `cascade` 2, `modules-files` 1, **`lsp-unreachable` 0**.

Both `lsp-unreachable` findings were refuted, and correctly so:

- `registry/__init__.py` `__all__` entries `_pool` / `_KNOWN_FACETS` — Pyright
  `reportUnsupportedDunderAll` false positive; resolved dynamically by the `_RegistryModule`
  proxy forwarding table at `registry/__init__.py:38-39`.
- `converters/__init__.py:29 _detect_pdf` — validly module-bound via the try/except
  `ImportError` pair at `docling_conv.py:332/337`.

`graph-degree` carries a self-reported **high systematic false-positive rate**: it models
neither by-reference passing (`execute=_erase_uploads`, `gate_fn=_gate_garbling`,
`asyncio.to_thread(self._run_page_index, ...)`, `add_middleware(BearerAuthMiddleware)`) nor
enum/member access on an imported name (`BlobKind.TREE_TEXT`, `AGPL_FALLBACK_TOTAL.labels()`).
~20 raw zero-degree hits were spot-checked and essentially all had real production callers.

**Conclusion: fixing the two lenses did not change the answer.** It raised confidence and
closed scope holes — it did not surface a hidden tier of dead code.

### 9.2 Genuinely new finds (not in run 1)

| Item | Lens | Risk |
|---|---|---|
| `registry/queries.py:299 list_all_doc_ids` | modules-files | medium |
| └ cascade: `registry/__init__.py:17` import + `:81` `__all__` | cascade | medium |
| `scripts/gates/build.sh:36 skip` | graph-degree | low |
| `script.py:182 infer_script` (public wrapper) | cascade | low |

`scripts/gates/` was never enumerated in run 1 — that hole is now closed. Sibling
`list_all_doc_ids_with_timestamps` **must be kept** (live via `registry_backfill/cleanup.py:63,65`).

### 9.3 Independent confirmation of run 1's critic-only finds

5 of the 8 `src/` symbols that only run 1's critic found are now independently CONFIRMED by a
lens + skeptic pass: `infer_script` alias (`garble.py:637`, not 638),
`_get_verdict_thresholds` (`types.py:533`, not 534), `LOGICAL_RANGES` (`script.py:26`),
`arabic_letter_ratio` (`script.py:131`, not 132), `_META_FIELDS` (`storage/verdict.py:37`).

The other **3 were silently dropped** by run 2 despite still being zero-reference — they
remain deferred, not cleared: `picture_plane.py:119 PictureRegion` (+2 properties),
`picture_plane.py:98 SkipReason.counts_in_enrichment_denominator`,
`heuristic_registry.py:103 HeuristicRegistry.all_entries`.

### 9.4 The `infer_script` trap

Two separate confirmed rows cover the same name and **must be resolved together**:
`garble.py:637` (the alias) and `script.py:182` (the public wrapper). The alias row's reason
asserts the wrapper "stays"; the cascade row marks the wrapper itself dead. Both are
production-dead; the wrapper is *test*-called (`tests/test_script.py:25,69`).

Removing both makes the name vanish entirely, so `helpers/__init__.py:104` (import) and
`:318` (`__all__`) must go too. **Preferred fix:** keep one — repoint `helpers/__init__.py`
at `script.infer_script` and delete only the garble alias. Underlying `_infer_script`
(`script.py:150`) is heavily live (`garble.py:574,705,717`; `tree_validation.py:295,413`)
and must not be touched.

### 9.5 `BULK_PROFILE` — the single `unsure`

Confirms §3's cascade finding independently. Production-dead since `helpers/verdict.py:12`
was removed; still reached by ~30 call sites across 8 test modules via the
`tests/_garble_compat.py` `check_garble(profile=...)` shim. Marked `unsure` rather than
`dead` because removal is a ~30-site test refactor, not a deletion. Sibling
`FLAT_MARKDOWN_PROFILE` and the `GarbleProfile` class stay live (`client/images.py:21-22,113,127-128`).

### 9.6 Known-uncovered surface after run 2

- **Obsidian MCP vault is empty** against this repo — re-confirmed this pass
  (`get_vault_stats` → `{"notes":0,"folders":0}`). All `docs-history` coverage rests on grep
  fallback. The script's claim that the vault root is the repo root does not hold here.
- **~600 of 688 zero-degree `Variable` nodes were never fetched** (`offset>400` never
  requested); only 5 were verified, and all 5 were false positives.
- `query_graph` Cypher is unreliable in this build for IN-list predicates *and* aggregations —
  a 33-name `IN` query returned 0 rows despite `trace_path` proving those `CALLS` edges exist.
- `ruff-static` deliberately excluded `tests/`; its 3 `ERA001` hits
  (`client/indexer.py:1496`, `memory_admission.py:41`, `metrics/definitions.py:207`) were
  never resolved to comment text. Pyright ran in **basic** mode, so `reportUnusedImport` etc.
  were off.
- `services/` closed by the critic, not the LSP lens: only 6 low-degree nodes across the three
  apps, all entry points (`health` ×3, `_verify_token` Depends callable, …). Nothing dead.
- 4 production-dead-but-test-called symbols a `max_degree=0` sweep cannot see:
  `HeuristicRegistry.get`, `HeuristicRegistry.list_expired`, `script.infer_script`,
  and the self-documented legacy shim `reset_verdict_thresholds` (`types.py:539-547`).

---

## 10. Run 2 removals applied

All 35 confirmed-dead items from §9 removed in one pass, plus 8 explicit cascades.
The `infer_script` conflict from §9.4 was resolved by the full-removal path: both the
`garble.py:637` alias and the `script.py:182` public wrapper are gone, and so are the two
tests (`from ... import infer_script` and `test_infer_script_latin`) that were its only
consumers. Nothing depended on the underlying `_infer_script`, which stays live.

### 10.1 src/ + scripts removals

| File | Removed |
|---|---|
| `src/pageindex_mcp/helpers/garble.py` | `infer_script = _infer_script` alias |
| `src/pageindex_mcp/helpers/types.py` | `_get_verdict_thresholds()` |
| `src/pageindex_mcp/script.py` | `LOGICAL_RANGES`, `arabic_letter_ratio()`, `infer_script()` wrapper |
| `src/pageindex_mcp/storage/verdict.py` | `_META_FIELDS` |
| `src/pageindex_mcp/registry/queries.py` | `list_all_doc_ids()` + `_LIST_ALL_DOC_IDS_SQL` (cascade) |
| `src/pageindex_mcp/registry/__init__.py` | `list_all_doc_ids` re-export + `__all__` entry |
| `src/pageindex_mcp/helpers/__init__.py` | `_get_verdict_thresholds`, `infer_script` (from `.garble`) + `__all__` entries |
| `scripts/gates/build.sh` | `skip()` function |

### 10.2 Tests removed (26 confirmed + cascades)

| File | Items removed |
|---|---|
| `test_converters_pipeline.py` | `_single_leaf`, `_mock_minio`, `_make_s3_error` (cascade), `_table_heavy_blocks` |
| `test_garble.py` | `_REAL_ARABIC` |
| `test_gates.py` | `_reason_policy_ok`, `CLIENT_PATH`, `from pathlib import Path` (cascade) |
| `test_helpers_combined.py` | `_KV_MD`, `_PROSE_MD`, `_garbled_window` |
| `test_recovery.py` | `_RECOVERY_GATES`, `_CONTINUOUS_OCR_DOC`, `_GOV_VISUAL`, `_GOV_LOGICAL` (cascade), `_ZERO_SCORE_LOGICAL_TEXT`, `_tree_from_lines`, `_PRE_RETRY_TEXT`, `_RETRY_REGRESSED_TEXT`, `_RETRY_IMPROVED_TEXT` |
| `test_registry.py` | `pytestmark_integration` |
| `test_rfc_quality.py` | `_diverse_words`, `_NON_EMPTY_STRUCTURE` |
| `test_rfc_tables.py` | `_padded_row`, `_MIN_ROWS` |
| `test_script.py` | `SRC_DIR`, `_HEX_ARABIC_RE`, `re`/`Path` imports (cascade), `infer_script` import (cascade), `TestInferScript` class (cascade) |
| `test_verdict.py` | `_borderline_ratio_tree`, `_other_s3error` |
| `test_registry_backfill.py` | `list_all_doc_ids` monkeypatch scaffolding (cascade: production stopped calling it) |

### 10.3 Verification

| Check | Result |
|---|---|
| pytest | **2055 passed**, 8 skipped, 2 xfailed, 1 xpassed, 0 failed (254s) — one fewer test than pre-pass (`test_infer_script_latin` removed with its subject) |
| ruff | 422 → **409** (net −13; further reductions gated on separate pre-existing violations) |
| diff | 28 files changed, 6 insertions(+), 592 deletions(−) |

### 10.4 Not applied (deliberately)

Three critic-found deferrals from Run 1 that Run 2 dropped without independent verification —
still zero-reference but no lens+skeptic confirmation:
`picture_plane.py:119 PictureRegion`, `SkipReason.counts_in_enrichment_denominator`,
`heuristic_registry.py:103 HeuristicRegistry.all_entries`. Would need a fresh Run 3 to
consider.

`BULK_PROFILE` (Run 2's single `unsure` row) also stays. Removal is a ~30-site test refactor
rewriting `tests/_garble_compat.py`'s `check_garble(profile=...)` shim, not a deletion —
not in scope for this pass.
