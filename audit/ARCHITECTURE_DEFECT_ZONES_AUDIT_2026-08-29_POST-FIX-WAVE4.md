# Architecture Defect Zones Audit — 2026-08-29 POST-FIX-WAVE4

**Date:** 2026-08-29  
**Run:** POST-FIX-WAVE4  
**Project:** PageIndex MCP Server  

---

## Summary Table

| # | Zone | Severity | Bug Count | Key Files (Sample) |
|---|------|----------|-----------|-------------------|
| 1 | ExtractionState route/ok multi-writer cascade | CRITICAL | 7 | helpers/types.py, client/indexer.py, client/recovery.py, helpers/gates.py, helpers/verdict.py |
| 2 | Normalize-before-detect null-detector lattice | CRITICAL | 6 | helpers/garble.py, script.py, helpers/tree_validation.py, client/indexer.py, client/recovery.py, helpers/gates.py |
| 3 | Split verdict authority: five writers over two stores | CRITICAL | 5 | storage/verdict.py, worker/registry_mirror.py, registry/queries.py, client/indexer.py, registry_backfill/backfill.py, promotion_sweep.py |
| 4 | Config-layer bifurcation | HIGH | 4 | config.py, helpers/gates.py, helpers/tree_split.py, client/indexer.py, client/recovery.py |
| 5 | Ordered-policy converter chain | HIGH | 4 | client/indexer.py, config.py, converters/pipeline.py, helpers/gates.py |
| 6 | Recovery dispatch: tuple-keyed dedup and unguarded raising normalizers | HIGH | 4 | helpers/gates.py, client/indexer.py, client/recovery.py, converters/ocr_langs.py, worker/errors.py |
| 7 | Divergent parallel garble/text accessors | HIGH | 4 | helpers/garble.py, helpers/flat.py, helpers/tree_validation.py, helpers/gates.py |
| 8 | Order-coupled erasure manifest with implicit inter-step data flow | MEDIUM | 2 | storage/documents.py, storage/hash_cache.py, storage/reconcile_etag.py, registry/queries.py |

**Total Critical Zones:** 3  
**Total High Zones:** 4  
**Total Medium Zones:** 1  
**Total Bug Count Attributed:** 32 defects across 8 zones

---

## Zone Details

### Zone 1: ExtractionState route/ok multi-writer cascade

**Severity:** CRITICAL | **Bug count:** 7

#### Mechanism

The ingestion pipeline threads a single mutable `ExtractionState` through a 10-gate evaluation, a GateSpec-driven recovery loop, two post-loop quality-check rerouters, and a match-based persistence dispatcher. `finalize_gate_and_route()` is documented as the "single writer" of `gate_result/ok/reason/first_defect/route`, but six other call sites write `state.route` and/or `state.ok` directly, leaving these five fields mutually inconsistent.

Every downstream consumer (reject reason, `all_defects` sidecar field, flat-vs-tree persistence, `LOW_QUALITY_TREES` metric label) reads a different subset of those fields, so any change to one stage silently reinterprets the others. A tree that passed every gate reaches the dispatcher with `ok=False`, `route=FLAT`, `first_defect=TreeDefect.OK` (value `''`), and `gate_result.ok=True`.

The recovery loop re-enters `finalize_gate_and_route` from inside recovery methods, coupling gate ordering and recovery ordering: any reorder of `GATES` changes which recovery sees which tree.

#### History

- Chain: RFC-040 D2 reordered the verdict promotion pipeline from six independent `_try_*` guards into a precedence-locked cascade (~8 documents shifted `MARGINAL/PASS → FAIL`)
- Chain: GATE_TABLE in `validate_tree` evaluates garbling (severity=0) before `node_count_lower_bound` (RFC-040 D4, open since OCR_IMAGE_BLOCK_CONFLATION_INVESTIGATION 2026-07-27)
- Chain: verdict.py fix #3 (cf904ff): `_try_image_enrichment` returns None instead of MARGINAL
- Chain: Verdict gate promotion cascade: `_try_cat_a` path lacked `effectively_garbled` guard
- Chain: OCR recovery eligibility check only inspected `first_defect`
- Chain: Four independent regression fixes ... combined effect ~40 documents re-evaluated
- Theme: Verdict/garble/OCR-recovery form a tightly coupled triad where a fix to one leg reliably flips outcomes in the others; discovered only via corpus re-ingestion audits, not pre-merge tests

#### Code Evidence

- **helpers/types.py:355-388** — `finalize_gate_and_route` docstring: "Single writer of gate_result/ok/reason/first_defect/route on *state*." Contradicted by direct assignments at:
  - **client/recovery.py:593, 649, 667, 685, 728-729, 758-759** — State route/ok assignments without finalize_gate_and_route
- **client/indexer.py:1473-1475** — Comment: "Quality checks (may override route intentionally - no re-derivation afterwards)"
- **client/indexer.py:1521** — Computes `_reject_reason = state.first_defect.value`; with TreeDefect.OK = '' the reject reason and Prometheus label are empty string on flat-prefer/landscape-reroute path
- **client/indexer.py:1279** — Writes `meta['all_defects']` from un-refreshed `state.gate_result`
- **client/indexer.py:1454-1470** — Recovery loop mutates state in place; relies on recovery methods calling `finalize_gate_and_route` internally

#### Key Files

- src/pageindex_mcp/helpers/types.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/helpers/verdict.py

---

### Zone 2: Normalize-before-detect null-detector lattice (presentation forms / NFKC)

**Severity:** CRITICAL | **Bug count:** 6

#### Mechanism

The Arabic presentation-forms signal (U+FB50-FDFF, U+FE70-FEFF) is destroyed by NFKC normalization early in the pipeline, but four independent detectors consume it downstream:
- Gate 7 (`_gate_bidi_degraded`)
- `detect_garble`'s PF recovery branch
- `_garble_check_nodes`' per-node contexts
- `_try_image_enrichment`'s garble re-check

Every fallback path that supplies the flag operates on post-NFKC text and therefore returns False structurally, not empirically. The detectors report zero violations, read as evidence of safety.

`had_presentation_forms` has exactly one true producer: `_renormalize_bidi_guarded` (indexer.py:180-183), which captures the flag pre-NFKC on the remote Docling path only, and returns `had_presentation_forms=False` when the bilingual Latin guard trips — exactly on mixed Arabic/English documents.

Every other producer is a fallback that cannot see the signal. `_infer_presentation_forms`' own docstring states "Post-NFKC this ratio is always 0", yet it is the flag source at 8 call sites. `decide_rtl` never assigns `had_presentation_forms`, so its RtlDecision always carries the dataclass default False. Recovery destroys the one live carrier: `_execute_ocr_retry` sets `state.rtl_decision = None` (recovery.py:332), and the recompute is structurally blind.

The NFKC-recovery heuristic inside `detect_garble` compares `_effective_script == 'Arabic'` (garble.py:583), but `_infer_script` returns only `'Arab'`, `'Latn'` or None. The fallback intended to recover the destroyed signal is dead code.

#### History

- Chain: ocr_langs.py fix #2 (cf904ff / RFC-040 D5) ... MOU MOHRE went PASS(Run19)→ERROR(Run20)
- Chain: garble.py fix #1 (cf904ff): improved Arabic presentation-forms/NFKC detection
- Chain: _check_bidi_coherence gate ... RFC-033 F2 still open at POST-FIX-WAVE3
- Chain: D2 Part A guard (_heading_is_logical_order)
- Chain: Garble detection kernel: presentation-forms fallback with NFKC blind spot in verdict.py removed
- Chain: Wave 2 triage spec for garble-detection kernel marked as redundant
- Theme: Null-detector pattern; multiple quality gates structurally cannot fire on their real failure mode; yet their zero violations measurement was historically read as evidence of safety and used to justify enabling stricter enforcement defaults

#### Code Evidence

- **helpers/garble.py:30-45** — `_infer_presentation_forms` docstring: "Post-NFKC this ratio is always 0... so callers on post-normalization text correctly get False"
- **helpers/garble.py:855, helpers/tree_validation.py:392, helpers/verdict.py:257, client/indexer.py:513/998/1024, client/images.py:135, converters/pictures.py:21** — Eight call sites depend on this False-returning fallback
- **script.py:694-708** — `RtlDecision.had_presentation_forms` defaults to False; `decide_rtl` never sets it
- **helpers/tree_validation.py:396-398** — Falls back to `decide_rtl(sig.flat_text)` when rtl_decision not threaded
- **client/recovery.py:332, 634-636** — Clear the only live carrier
- **helpers/garble.py:583** — Literal "Arabic" appears exactly once; script.py:174 shows `_infer_script` returns only 'Arab'/'Latn'/None
- **client/indexer.py:155-161** — Bilingual guard returns `RtlDecision(method='bilingual_guard_skip')` with flag left False

#### Key Files

- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/script.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/helpers/gates.py

---

### Zone 3: Split verdict authority: five writers over two stores

**Severity:** CRITICAL | **Bug count:** 5

#### Mechanism

The authoritative verdict for a document is written by five independent code paths across two stores (MinIO sidecar `processed/<id>.meta.json` and Postgres `doc_registry` row), with only one of them enforcing the max-priority-wins CAS. The others are unconditional read-merge-writes.

The verdict is computed inside the isolated `converters_cli` child, which has no Postgres pool, so `_persist_tree_result` / `_persist_flat_result` write optimistically and unconditionally to the sidecar via `save_doc_meta`. The worker parent runs `_upsert_registry_row`, which performs the real CAS (`_UPSERT_SQL` with VERDICT_PRIORITY guard) and best-effort backfills the sidecar — the second write, authoritative and capable of silently reverting the first.

Three further writers exist: `registry_backfill/reconcile.py`, `registry_backfill/backfill.py` (sidecar-only self-heal, no CAS), and `promotion_sweep.py` (save_doc_meta + upsert_doc).

The false belief: `save_doc_meta` itself arbitrates. The docstring at registry_backfill/backfill.py:145 states "The CAS guard in save_doc_meta protects against clobbering a newer verdict", but `save_doc_meta` contains no priority comparison at all — it merges 'verdict' from meta over existing in a plain `_MERGE_FIELDS` loop.

Layered on top: `force_verdict_override` selects `_UPSERT_OVERRIDE_SQL`, bypassing the priority CAS while keeping the processed_at CAS. There are two arbitration semantics per table.

A re-ingestion producing lower-priority verdict is discarded by Postgres but may still land in the sidecar, and corpus audits reading sidecars see a different verdict than the registry.

#### History

- Chain: Dual verdict-arbitration guards intended to prevent stale writes: MinIO sidecar consensus check (RFC-037 D1) and Postgres read-back validation (RFC-037 D5) ... unresolved into POST-FIX-WAVE3 new zone
- Chain: HR3 boot-time ZDR compliance gate (same boot-vs-per-call split pattern)
- Chain: Erasure cascade missing explicit tracking
- Theme: Fix-one-instance-miss-the-other duplication ... verdict priority maps were duplicated (_LEDGER_VERDICT_PRIORITY vs _LEDGER_PRIORITY, RFC-037); dual verdict-arbitration CAS guards used inconsistent comparison operators (> vs >=, RFC-037)
- Theme: Threshold/config tightening repeatedly masquerades as content regression in corpus audits ... live-store verification against actual MinIO state is required

#### Code Evidence

- **storage/verdict.py:78-190** — `save_doc_meta` verified by grep: VERDICT_PRIORITY and any priority comparison is absent; only mention of "priority" is docstring line 98 describing Postgres
- **registry_backfill/backfill.py:145** — Asserts CAS guard in `save_doc_meta` that does not exist
- **worker/registry_mirror.py:150-175** — `upsert_doc` performs CAS then "best-effort sidecar backfill with the winning Postgres values"; second write to same key
- **client/indexer.py:1302 (tree), ~1141 (flat)** — Call `save_doc_meta` from child process
- **registry/queries.py:86-127** — Defines `_UPSERT_VERDICT_CAS` and `_UPSERT_VERDICT_OVERRIDE` as two alternative UPDATE bodies selected at queries.py:155
- **Additional writers:** registry_backfill/reconcile.py:76-82, registry_backfill/backfill.py:161/:323, promotion_sweep.py:124/:141

#### Key Files

- src/pageindex_mcp/storage/verdict.py
- src/pageindex_mcp/worker/registry_mirror.py
- src/pageindex_mcp/registry/queries.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/registry_backfill/backfill.py
- promotion_sweep.py

---

### Zone 4: Config-layer bifurcation: frozen snapshot vs live os.environ

**Severity:** HIGH | **Bug count:** 4

#### Mechanism

`config.py` builds a frozen `PipelineConfig` snapshot from 88 env reads at import time, asserts cross-flag coupling invariants against it, and serializes a subset into every sidecar as the `effective_config` audit trail. But 55 further `os.environ`/`os.getenv` reads live in 24 other modules, several re-reading the same variables at call time with different parsing rules.

Three verified divergences:

1. **bidi_coherence_enforce**: Defined (config.py:397, 506) and included in sidecar effective_config field list (config.py:705) but has zero consumers. The gate it supposedly controls reads `os.environ` directly with different truthiness semantics: `_envbool` accepts {'1','true','yes'} while gates.py:162 requires exactly 'true'. Setting `BIDI_COHERENCE_ENFORCE=1` records `enforce=True` in sidecar while disabling Gate 7 at runtime.

2. **LEAF_SPLIT_RATIO**: Snapshotted into `pipeline_config.leaf_split_ratio` and subject of import-time coupling assertion (config.py:597-600, `PASS_MAX_LEAF_RATIO <= LEAF_SPLIT_RATIO`), yet `tree_split.py:385` re-reads `float(os.environ.get('LEAF_SPLIT_RATIO','0.30'))` at call time. The assertion guards a value the splitter does not use.

3. **PRE_GARBLE_FORCE_OCR_ENABLED**: Snapshotted at config.py:488 and independently re-read at indexer.py:530. Because `pipeline_config` is frozen at import while direct reads are live, a process that mutates `os.environ` gets two different answers.

This makes threshold refactors look like content regressions: the audit artifact and executing code disagree.

#### History

- Chain: Zone 7 config-layering fix (commit 610d078): refactored frozen threshold constants from local scopes into unified _CONFIG scope, revealing DEPTH_ADEQUACY_FLOOR and CHAR_FLOOR had drifted by 1-2 units ... changed verdict outcomes for ~20 documents
- Theme: Hardcoded constants and fallback boolean flags scattered across files cause invisible asymmetries and prevent unified testing
- Theme: Threshold/config tightening repeatedly masquerades as content regression in corpus audits

#### Code Evidence

- **config.py:353** — `_envbool` returns membership in ('1','true','yes')
- **helpers/gates.py:162** — `if os.environ.get("BIDI_COHERENCE_ENFORCE", "true").lower() != "true": return (False, "")`
- **grep confirmation** — `bidi_coherence_enforce` has zero consumer outside config.py (definition :397, assignment :506, sidecar field list :705)
- **config.py:511** — `leaf_split_ratio=float(os.environ.get('LEAF_SPLIT_RATIO','0.30'))` with assertion at :597-600
- **helpers/tree_split.py:382-386** — Re-reads LEAF_CONCENTRATION_PARAGRAPH_SPLIT_ENABLED (different false-set {'false','0','no','off'}) and LEAF_SPLIT_RATIO
- **config.py:488 vs client/indexer.py:529-530** — PRE_GARBLE_FORCE_OCR_ENABLED dual reads
- **Counts:** 88 os.environ.get in config.py, 55 in 24 other modules
- **config.py:686-728** — Builds effective_config sidecar payload from frozen dataclass only

#### Key Files

- src/pageindex_mcp/config.py
- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/helpers/tree_split.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/client/recovery.py

---

### Zone 5: Ordered-policy converter chain with load-bearing branch order

**Severity:** HIGH | **Bug count:** 4

#### Mechanism

The PDF converter fallback chain resolves each failure through a five-way if/elif ladder producing a `ConverterFailurePolicy` (RETRY / BLOCK_AGPL / GATE_AGPL_STRUCTURAL / REJECT / WALK). The classification order is explicitly documented as load-bearing, and the licensing guarantee (HR4: never walk into an AGPL converter on transient outage) depends entirely on that order — which the RETRY branch defeats.

The ladder at indexer.py:670-677 tests RETRY first: `if _is_transient and _transient_attempts < CONVERTER_TRANSIENT_RETRY_COUNT`. The RETRY handler's comment claims it will "rewind idx so the for-loop re-enters this entry", but the implementation is a bare `continue` inside `for idx, entry in enumerate(chain)` — which advances to the NEXT chain entry, not the same one.

With shipped default `CONVERTER_TRANSIENT_RETRY_COUNT=1` (config.py:522), the first transient failure of the primary converter always walks one step down the chain. If the next entry is the AGPL converter it executes it — bypassing the BLOCK_AGPL branch that exists precisely to prevent that, because BLOCK_AGPL is only reachable once `_transient_attempts` has been exhausted.

The "no retry actually happened" bug and the "HR4 licensing guarantee is unenforced" bug are the same line.

The zone's generative mechanism: policy is derived by falling through an ordered predicate ladder over four independent inputs with two orthogonal env toggles. Inserting or reordering any branch silently redefines the others.

#### History

- Chain: allow_agpl_fallback gating and AGPL_FALLBACK_TOTAL metric added to prevent silent route-through to AGPL converters ... ISS-35 (2026-07-15) partial fix regressed at POST-FIX-WAVE3 because gating is a binary toggle, not toggle+remediation
- Chain: AGPL converter structural failures fell through silently without gating or metrics (RFC-018 P2)
- Chain: JOB_TIMEOUT raised to 3630s (RFC-028) and 16.5x inspector multiplier calibrated (RFC-032)
- Chain: PDF_INSPECTOR_PRECLASSIFY confidence gate (RFC-031/032)
- Theme: Remote/external-service code drift is a repeated generative mechanism ... no version/contract pinning pattern appears in Docling, PDF Inspector and arq
- Theme: Silent degradation patterns: failures fall through unnamed else branches without metrics or gates

#### Code Evidence

- **client/indexer.py:657-661** — Comment: "Ordering is load-bearing: the AGPL branches must be classified BEFORE the generic end-of-chain/WALK branches"
- **client/indexer.py:670-677** — The ladder; RETRY handler comment says "rewind idx" but only does `continue`
- **client/indexer.py:692-702** — RETRY handler whose only control-flow statement is `continue` — loop variable idx is bound by enumerate() and cannot be rewound
- **config.py:521-522** — `CONVERTER_TRANSIENT_RETRY_COUNT` default '1'
- **client/indexer.py:479-484** — D3a pre-conversion probe skipped when ALLOW_AGPL_FALLBACK=false; the same branch that populates state.pdf_page_count required for Gate 10

#### Key Files

- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/config.py
- src/pageindex_mcp/converters/pipeline.py
- src/pageindex_mcp/helpers/gates.py

---

### Zone 6: Recovery dispatch: tuple-keyed dedup and unguarded raising normalizers

**Severity:** HIGH | **Bug count:** 4

#### Mechanism

The GateSpec recovery loop deduplicates by the `recovery_fns` tuple rather than by individual method name, so a method listed in two different tuples runs twice. None of the OCR recovery methods check whether full-page OCR has already been applied.

`gates.py` declares NODE_COUNT_LOW with `recovery_fns=('_recover_low_content_ocr','_recover_image_dominant_ocr')` and DEPTH_LOW with `recovery_fns=('_recover_image_dominant_ocr',)`. The loop's dedup set is typed `set[tuple[str, ...]]` and tests `_gate.recovery_fns in _fired_recovery`, so these two distinct tuples both fire and `_recover_image_dominant_ocr` executes twice for any document where both gates fire — the common case since node_count<3 almost always has depth<2.

Each execution is a full-page OCR re-extraction of the whole PDF, yielding up to three full-page OCR passes per document. Jobs timeout before `save_doc` and leave zero MinIO artifact.

Second half: `ensure_tessdata` now raises `TessdataUnavailableError` for unavailable non-Latin scripts. Inside `_execute_ocr_retry` it is caught by broad `except Exception` and degrades to a metric, but at indexer.py:885 (the image-extension branch) it is called bare, converting a language-availability problem into a terminal job error with no persisted artifact.

#### History

- Chain: ocr_langs.py fix #2 (commit cf904ff / RFC-040 D5): ensure_tessdata now raises TessdataUnavailableError instead of silently substituting deu/eng
- Chain: _OCR_ESCALATION single kill-switch ... RFC-018 P1/P3 remain unresolved through POST-FIX-WAVE3
- Chain: OCR recovery eligibility check only inspected first_defect
- Chain: JOB_TIMEOUT raised to 3630s (RFC-028)
- Theme: Multi-site fixes that consistently miss 1-2 locations, requiring follow-up commits and AST guards
- Theme: Compensating heuristics are repeatedly substituted for the actual proposed fix

#### Code Evidence

- **helpers/gates.py:368-386** — GateSpec(NODE_COUNT_LOW, recovery_fns=('_recover_low_content_ocr','_recover_image_dominant_ocr')) vs GateSpec(DEPTH_LOW, recovery_fns=('_recover_image_dominant_ocr',))
- **client/indexer.py:1454-1459** — `_fired_recovery: set[tuple[str, ...]] = set()` and dedup is on the tuple, not the member
- **client/recovery.py:458-498** — `_recover_image_dominant_ocr` guards only on state.ok / ext / flag / image-line ratio
- **client/recovery.py:232-236** — `_execute_ocr_retry` docstring mentions full_page_already_applied only as something 'callers should set'; function never reads it
- **client/indexer.py:885** — `img_langs = await asyncio.to_thread(ensure_tessdata, detect_ocr_langs(filename))` with no enclosing try
- **client/recovery.py:273** — Inside try whose except at :375 swallows it
- **converters/ocr_langs.py:123,161,174,186** — Raise sites
- **worker/errors.py:30** — Maps TessdataUnavailableError → ChildErrorClassification('converter_env_missing', terminal=True)

#### Key Files

- src/pageindex_mcp/helpers/gates.py
- src/pageindex_mcp/client/indexer.py
- src/pageindex_mcp/client/recovery.py
- src/pageindex_mcp/converters/ocr_langs.py
- src/pageindex_mcp/worker/errors.py

---

### Zone 7: Divergent parallel garble/text accessors

**Severity:** HIGH | **Bug count:** 4

#### Mechanism

Garble is decided by three procedures meant to be consolidated but were not: `detect_garble` (the declared "sole public entry point"), a raw `garble_prongs` call inside `_garble_check_nodes`' whole-tree fallback, and `_garble_check_flat_blocks`. The same shape recurs in flat-block text extraction, where role-typed blocks omit the 'text' key and three separate accessors each re-implement the role dispatch.

`detect_garble` wraps `garble_prongs` with real policy: re-infers dominant script when None, applies RFC-025 D2 short-text rule, selects normalization blob kind, and critically runs a presentation-forms RECOVERY heuristic that can flip `_had_pf` from False to True (garble.py:577-587).

The whole-tree fallback inside `_garble_check_nodes` (garble.py:747-757) bypasses all of that: it calls `normalize_for_garble` then `garble_prongs` directly, passing `script_context.had_presentation_forms` through verbatim. The aggregate path is strictly less sensitive to exactly the signal RFC-040 D5 was about, and the two procedures cannot be kept in sync by construction — only by reviewer notice.

The flat side repeats it: helpers/flat.py emits blocks where `role=='table'` and `role=='image'` carry no 'text' key by design (RFC-022 B3), and `_flat_block_primary_text`, `_flat_search_text` and the table-branch at flat.py:242 each contain their own role dispatch to recover missing content.

Zone-9 header-only-table fix had to be applied inside `_flat_block_primary_text` alone; any measurement or audit path reading `block['text']` directly still registers zero table content.

#### History

- Chain: Garble detection consolidation: duplicate tree/flat implementations merged ... RFC-040 D3/D6 consolidation eliminates one branch ... marked partial
- Chain: route_and_extract_flat table-role blocks intentionally omit text key ... measurement tooling that counts text key presence will register zero table content
- Chain: garble.py fix #1
- Chain: Wave 2 triage spec for garble-detection kernel marked as redundant
- Theme: Fix-one-instance-miss-the-other duplication is chronic: garble digit-ratio floor was duplicated between _tree_is_garbled/_flat_text_is_garbled (ISS-36) and again between garble_prongs and _garble_check_nodes fallback (RFC-040 2a)
- Theme: Shared choke points in converters create cascading regressions

#### Code Evidence

- **helpers/garble.py:529-540** — `detect_garble` docstring: "Unified garble evaluation entry point (Zone-3). Single-surface API: all garble heuristics ... run inside garble_prongs"
- **helpers/garble.py:745-757** — `_is_toplevel` fallback calls normalize_for_garble + garble_prongs directly, skipping the _had_pf recovery at :577-587 and short-text rule at :565-571
- **helpers/garble.py:766+** — Third procedure `_garble_check_flat_blocks`
- **helpers/flat.py:87-95, :116-126** — Construct table/image blocks with no 'text' key
- **helpers/flat.py:184-197** — `_flat_block_primary_text` carrying Zone-9 header-only-table fix
- **helpers/flat.py:205-216** — `_flat_search_text`
- **helpers/flat.py:242** — Role dispatch in another location

#### Key Files

- src/pageindex_mcp/helpers/garble.py
- src/pageindex_mcp/helpers/flat.py
- src/pageindex_mcp/helpers/tree_validation.py
- src/pageindex_mcp/helpers/gates.py

---

### Zone 8: Order-coupled erasure manifest with implicit inter-step data flow

**Severity:** MEDIUM | **Bug count:** 2

#### Mechanism

The HR2 right-to-erasure cascade was refactored into a declarative `_ERASURE_MANIFEST` of eleven `ErasureStep` entries driven by a generic loop, which reads as order-independent. It is not: two steps discover data that later steps require, so reordering or removing an entry silently converts a purge into a no-op that reports success.

`ErasureContext` is mutable state threaded through the manifest, and two fields are discovered mid-cascade rather than up front:

1. `ctx.doc_name` is recovered inside `_erase_uploads` (step 1) from the uploads listing when the processed artifact is already gone. Steps 5 (hash_cache) and 7 (preloaded) are unreachable without it and merely log a warning and return False.

2. `ctx.sha256` is read inside `_erase_verdicts` (step 2d) from `processed/<id>.meta.json`, which step 3 then deletes. The verdict ledger at `verdicts/<sha256>.json` can only ever be purged if 2d runs before 3.

Both dependencies are documented only in prose and in the manifest's ordering; there is no assertion, no dependency declaration. The driver comment actively invites reordering ("Adding a derived store is a one-line entry here...").

Steps that most often fail to reach their store are marked `required=False` — verdicts, preloaded, figures, flat_json — so an unreached ledger purge is logged at DEBUG as an "expected miss" and `delete_doc` returns `{'errors': []}`, an apparently clean HR2 cascade with residual PII-derived artifact.

#### History

- Chain: ISS-02 (delete_doc fire-and-forget registry delete) ... fix was applied twice to different code paths, revealing erasure logic was duplicated
- Chain: Erasure cascade missing explicit tracking — no way to verify MinIO write paths are fully purged during deletion
- Theme: HR2 (erasure) and HR3 (PII/ZDR egress) compliance gaps recur every time a new storage location or new LLM call site is introduced
- Theme: New storage or new LLM paths automatically inherit the old blind spots without triggering codebase-wide audit

#### Code Evidence

- **storage/documents.py:404-411** — `_erase_uploads` recovers ctx.doc_name from object basename inside step 1
- **storage/documents.py:475-481, :532-537** — Return False with only a warning when ctx.doc_name is None
- **storage/documents.py:405-408** — `_erase_verdicts` docstring: "Must run before the sidecar is deleted (step 3) because the sha256 that keys the ledger lives only in processed/<doc_id>.meta.json"
- **storage/documents.py:425-430** — Returns False when sha256 unavailable; manifest marks step required=False so storage/documents.py:239-244 logs the miss at DEBUG
- **storage/documents.py:255-258** — Returns `{'errors': ctx.errors}` with empty list
- **storage/documents.py:546-549** — Driver comment inviting one-line manifest additions
- **storage/documents.py:276-292** — ErasureContext dataclass with two discovered fields

#### Key Files

- src/pageindex_mcp/storage/documents.py
- src/pageindex_mcp/storage/hash_cache.py
- src/pageindex_mcp/storage/reconcile_etag.py
- src/pageindex_mcp/registry/queries.py

---

## Cross-Cutting Themes

### 1. Null-Detector Pattern

Multiple quality gates (bidi coherence presentation-forms check, garble-gate Latin-gibberish blind spot, digit-ratio floor below 500 chars) structurally cannot fire on their real failure mode — the signal they test for is destroyed (NFKC decomposition) or excluded (line-selector range, char floor) before the check runs — yet their "zero violations" measurement was historically read as evidence of safety and used to justify enabling stricter enforcement defaults (e.g., BIDI_COHERENCE_ENFORCE=true).

### 2. Threshold/Config Tightening as Content Regression

Threshold/config tightening repeatedly masquerades as content regression in corpus audits. Run-20's D4 pre-publish live-store re-verification found that reported "structural collapses" (505→80 nodes) were in fact unchanged trees whose verdict flipped purely because a config-layering refactor (610d078) silently raised depth-adequacy or char-floor thresholds — underscoring that live-store verification against actual MinIO state, not just dispatched figures, is required before attributing a verdict change to extraction damage.

### 3. Fix-One-Instance-Miss-the-Other Duplication

Fix-one-instance-miss-the-other duplication is a chronic, cross-cutting defect generator: the garble digit-ratio floor was duplicated between `_tree_is_garbled`/`_flat_text_is_garbled` (ISS-36) and again between `garble_prongs` and `_garble_check_nodes`' fallback (RFC-040 2a); verdict priority maps were duplicated (`_LEDGER_VERDICT_PRIORITY` vs `_LEDGER_PRIORITY`, RFC-037); dual verdict-arbitration CAS guards used inconsistent comparison operators (> vs >=, RFC-037). Each recurrence independently produced a defect zone.

### 4. Partial RFC Implementation as Net-Negative

Partial RFC implementation is actively net-negative across consecutive remediation waves. POST-FIX-WAVE3 analysis shows RFC-033/037/040/039 all contain marked "unresolved" sections (F2 open, D5 incomplete, D4 unpublished, D6 partial); each wave propagates these incompleteness markers into the next audit cycle without triggering automatic follow-up or roll-back, accumulating "partial fixes" that masquerade as progress.

### 5. Remote/External-Service Code Drift

Remote/external-service code drift is a repeated generative mechanism: the same "no version/contract pinning" pattern appears in Docling (bidi reconstruction drift, table detection fallback), PDF Inspector (confidence scoring calibration), and arq (job serialization format change between 0.14→0.15 breaking on-disk jobs). Each recurrence required manual rediscovery and patch.

### 6. Compliance Cascades on New Storage/LLM Paths

HR2 (erasure) and HR3 (PII/ZDR egress) compliance gaps recur every time a new storage location or new LLM call site is introduced: ISS-02 fixed erasure in the "happy path" registry delete; ISS-41 found the same gap in a parallel `_cleanup_artifact` path; RFC-011/RFC-039 added boot-time ZDR gates but per-call gates were incomplete. New storage or new LLM paths automatically inherit the old blind spots without triggering codebase-wide audit.

### 7. Verdict/Garble/OCR-Recovery Coupling

Verdict/garble/OCR-recovery form a tightly coupled triad where a fix to one leg reliably flips outcomes in the others: raising the char-floor closes the Latin-only escape hatch but flips documents that border on char-floor from MARGINAL → FAIL; fixing the garble-gate pre-NFKC order changes which documents are tagged GARBLED; reordering the verdict cascade changes precedence. The three systems lack a common test oracle, so interactions are discovered only via corpus re-ingestion audits, not pre-merge tests.

### 8. Compensating Heuristics as Entrenchment

Compensating heuristics are repeatedly substituted for the actual proposed fix, then themselves become entrenched and require removal: the image-enrichment bypass was meant as a temporary bridge until OCR recovery improved; it was never removed and became a leniency vector; RFC-040 D1 removes it, but another compensating heuristic (`_has_image_rescue` guard) was added and also left incomplete.

### 9. Audit/Measurement Tooling Blind Spots

Audit/measurement tooling shares the same blind spots as the code it audits: table-role blocks intentionally omit 'text' keys (RFC-022 B3), so flat-extraction metrics that count 'text' keys register zero tables; NFKC decomposition happens before garble detection checks for presentation-forms, so the audit count of "presentation-forms violations" is zero even if the blind spot exists. Fixing the code does not automatically fix the measurement.

### 10. Shared Choke Points and Cascading Regressions

Shared choke points in converters and verdict pipeline create cascading regressions when refactored: the bidi reconstruction in Docling (external) feeds into `_check_bidi_coherence` gate (verdict.py); when the Docling API signature changed between container versions, the gate broke silently. Similarly, `garble_check_nodes` is called from both tree validation and flat extraction; a fix to one path does not reach the other without explicit duplication or refactor.

### 11. Hardcoded Constants and Scattered Flags

Hardcoded constants and fallback boolean flags scattered across files cause invisible asymmetries and prevent unified testing: DEPTH_ADEQUACY_FLOOR=4 in one branch, 5 in another (before Zone 7 refactor); `_LEDGER_VERDICT_PRIORITY` vs `_LEDGER_PRIORITY` constants in parallel modules; `allow_agpl_fallback` flag has no remediation logic, just a metric. Unified config layer (Zone 7) partially addressed this but coverage is incomplete.

### 12. NFKC Normalization Blind Spot

NFKC normalisation blind spot in garble detection: presentation-forms are lost before detection sees text. The garble check runs after NFKC decomposition (in route_and_extract), but presentation-forms are destroyed by decomposition, so the detector never sees them. RFC-040 D5 reorders the check pre-NFKC, but similar normalization-before-check patterns recur in bidi and OCR recovery logic.

### 13. Silent Degradation via Unnamed Else Branches

Silent degradation patterns: failures fall through unnamed else branches without metrics or gates. Converter structural failures in route_and_extract (RFC-018) have no explicit handling; low-confidence OCR decisions (RFC-032) had no gate until RFC-031; low-image-count documents (RFC-022) had no measurement. Each gap was discovered only when corpus regression revealed the silently-degrading documents.

### 14. Caching Strategies with Incomplete Invalidation

Caching strategies that gated only first operation then ignored state for all subsequent operations: the confidence cache for PDF_INSPECTOR_PRECLASSIFY was written once and never invalidated, so if a re-extraction flag was set, the cached low-confidence result blocked the attempt. RFC-031 added a cache-bypass flag, but similar patterns in Redis cache invalidation and MinIO etag checks are incomplete.

### 15. Test Fixture Consolidation Backward-Compatibility Gap

Test file consolidation introduced backward-compatibility gap requiring normalisation layer: 85 test files were merged to 37; fixture names collided; Redis/MinIO URL formats differed between test setups. The normalization layer (test/helpers.py) abstracts over these differences, but the abstraction leaked into production code paths (e.g., env-var reading for test mode).

### 16. Audit Zones Describing Completed Work

Audit zones describing work that was already completed, creating risk of redundant remediation: Zone 2 (2026-08-29 triage) describes four garble-detection kernel fixes that were all completed in RFC-040 D3/D5/D6; but the zone file itself was not retired, and POST-FIX-WAVE3 risks re-implementing the same fixes.

---

## Recommended Actions

1. **Immediate (Critical):** Audit zones 1-3 require architectural refactoring to unify verdict decision-making, eliminate multi-writer patterns, and establish single sources of truth for state mutations.

2. **High Priority (High):** Zones 4-7 require config-layer consolidation, recovery loop dedup semantics audit, and garble/text accessor unification.

3. **Medium Priority (Medium):** Zone 8 requires explicit dependency declarations in the erasure manifest and compliance-path auditing on every new storage/LLM integration point.

4. **Process Improvement:** Establish pre-merge test oracle for verdict/garble/OCR interactions; implement RFC completeness tracking; require live-store verification for verdict changes in corpus audits.

---

**Audit Report Generated:** 2026-08-29  
**Format Version:** 1.0

---

## Simplification Proposals

### Zone 1: ExtractionState route/ok multi-writer cascade

1. Core simplification: Make finalize_gate_and_route the ONLY code path that can set state.route/ok/gate_result/first_defect by giving it an explicit 'recovery_override' parameter, then delete every direct `state.route = ...` / `state.ok = ...` assignment in recovery.py and replace them with calls through this one function. This turns 'gate ordering and recovery ordering are coupled' into 'recovery always re-derives through the same function gates use', which also fixes the empty-string reject-reason and stale all_defects bugs for free since first_defect/gate_result stay consistent by construction.

2. Concrete restructuring steps:
   - helpers/types.py: extend finalize_gate_and_route(state, recovery_override: Route | None = None, recovery_reason: str | None = None) -> writes route/ok/first_defect/gate_result together, using recovery_reason (not first_defect.value) as the Prometheus/error label when an override is present. (+15 lines)
   - client/recovery.py: replace the six direct writes (593, 649, 667, 685, 728-729, 758-759) with `finalize_gate_and_route(state, recovery_override=Route.FLAT, recovery_reason='recovery_<method>')`. (-15/+10 lines, net near-zero, but removes the divergent writers)
   - client/indexer.py: delete the 'no re-derivation afterwards' comment block (1473-1475) since it's no longer true; fix `_reject_reason` (1521) to prefer `state.gate_result.reason` over `first_defect.value` so an override path never surfaces `''`. (-8/+5 lines)
   - client/indexer.py:1279: no code change needed once gate_result is always current, but add an assertion in tests that meta['all_defects'] matches state.gate_result post-recovery.
   Rough net delta: ~+20/-25 lines, five fewer independent writers.

3. Historical bug classes prevented: RFC-036 D3 / RFC-029 D1 / RFC-033 D8 / RFC-035 D2-style 'add another override' patches; LowQualityTreeError('') / empty Prometheus label on the flat-prefer and landscape-reroute paths; sidecar recording a clean tree (all_defects) for a document actually rejected as low-quality.

4. Migration risk: medium. Recovery methods currently rely on directly mutating state as a side effect within the recovery loop; each of the six call sites needs its own regression test (rtl_flat_compare, vlm_fallback x3, flat_prefer, landscape_reroute) confirming route=FLAT and a non-empty reason land correctly. Sequence: add the override parameter and helper without removing old writes; add a debug-mode assertion that the old and new values agree; migrate call sites one at a time behind that assertion; remove the assertion and old writers once all six are converted and a full corpus run is green.

5. Estimated effort: 2-3 engineer-days including regression tests for all six recovery paths.

### Zone 2: Normalize-before-detect null-detector lattice (presentation forms / NFKC)

1. Core simplification: Capture had_presentation_forms exactly once, pre-NFKC, as an immutable signal on ExtractionState set by _renormalize_bidi_guarded, and thread that single value everywhere instead of letting 8 call sites re-derive it post-normalization (where it is structurally always 0/False per _infer_presentation_forms' own docstring). Delete the post-NFKC fallback function and the dead 'Arabic' string comparison; require decide_rtl to take the captured flag as a mandatory argument instead of defaulting to False.

2. Concrete restructuring steps:
   - client/indexer.py:155-161 (bilingual guard): compute the real pre-NFKC ratio before short-circuiting instead of returning a False sentinel, and store it on state as `state.presentation_forms_signal` (a field independent of rtl_decision so recovery can't null it out). (+10 lines)
   - script.py: change `decide_rtl(text, had_presentation_forms)` to require the signal as a parameter (no default), threading state.presentation_forms_signal through every caller (validate_tree, verdict.py:257, gates.py). (-1/+1 default removal, ~6 call-site edits)
   - helpers/garble.py: delete `_infer_presentation_forms`'s post-NFKC fallback branch (the function whose own docstring says the ratio is always 0 post-normalization) and all 8 call sites that use it (tree_validation.py:392, verdict.py:257, garble.py:855, indexer.py:513/998/1024, images.py:135, pictures.py:21) — replace each with a read of state.presentation_forms_signal. Fix garble.py:583 `_effective_script == "Arabic"` to `== "Arab"` (matching what script.py actually returns) so the NFKC-recovery heuristic stops being dead code. (-40/+25 lines)
   - client/recovery.py:332, 634-636: stop nulling state.rtl_decision to force a recompute; recompute rtl_decision as needed but always pass through the untouched state.presentation_forms_signal so the recompute isn't structurally blind. (-4/+6 lines)
   Rough net delta: ~-35/+45 lines: one fallback function removed, one field added and threaded consistently, one string bug fixed.

3. Historical bug classes prevented: PASS→ERROR and MARGINAL→ERROR regressions on Arabic/bilingual documents when a detector starts firing correctly (the RFC-040 D5 pattern) while sibling detectors stay blind; Gate 7/Gate 4 evaluating a signal that is provably always false post-NFKC; the dead NFKC-recovery branch in detect_garble that could never trigger due to the 'Arabic' vs 'Arab' typo.

4. Migration risk: medium-high — touches 8 call sites across 5 files and directly affects the Run-20 regression class. Requires a corpus regression pass over the Arabic/bilingual document set both before and after. Sequence: add state.presentation_forms_signal alongside the existing (broken) detectors, dual-log any divergence for one release cycle without changing gate behavior, then cut gates over to the new field one at a time, then delete the dead fallback function and the 'Arabic' typo fix last (once no test depends on the dead branch never firing).

5. Estimated effort: 3-4 engineer-days including a full corpus regression run against the Arabic/bilingual validation set.

### Zone 3: Split verdict authority: five writers over two stores

1. Core simplification: Stop treating the sidecar as an independent verdict store. The converters_cli child has no Postgres access and therefore no basis to arbitrate priority, so it should never write 'verdict' at all; only the worker parent's CAS-guarded upsert (_upsert_registry_row) may write verdict, and it alone is responsible for mirroring the winning value back to the sidecar — collapsing five writers down to one authority with one mirror.

2. Concrete restructuring steps:
   - storage/verdict.py save_doc_meta: remove 'verdict' from the mergeable field set entirely, so the converters_cli child (client/indexer.py:1302 and ~1141) can no longer write it, even optimistically. (-15/+5 lines)
   - worker/registry_mirror.py: change the existing 'best-effort sidecar backfill with the winning Postgres values' (:150-175) from best-effort to required-with-retry, since it becomes the sole writer of verdict into the sidecar. (+10 lines)
   - registry_backfill/backfill.py: delete the false claim at :145 ('the CAS guard in save_doc_meta protects...') and delete the sidecar-only self-heal writes at :161 and :323; replace with a call into the same CAS+mirror path used by registry_mirror.py. (-40/+10 lines)
   - promotion_sweep.py:124, 141: remove direct save_doc_meta(verdict=...) writes; route verdict changes through the CAS upsert + mirror function instead. (-15/+8 lines)
   - registry/queries.py: leave _UPSERT_VERDICT_CAS and _UPSERT_VERDICT_OVERRIDE distinct (override is a legitimate separate semantic for force_verdict_override), but document clearly which callers may use which, now that only two writers exist instead of five.
   Rough net delta: ~-70/+35 lines; three of five writers deleted outright.

3. Historical bug classes prevented: sidecar recording a stale/lower-priority verdict after Postgres CAS correctly rejected it; corpus audits reading a different verdict from sidecars than from the registry; registry_backfill silently clobbering a newer CAS-won verdict because its 'protection' comment described behavior save_doc_meta never had.

4. Migration risk: low-medium — the converters_cli child never had DB access anyway, so removing its ability to write verdict is a pure bug fix with no functional loss; the main risk is any downstream code that reads the sidecar verdict before the parent's mirror backfill has run (a timing window that already exists today, just made more visible). Sequence: first make save_doc_meta log-but-ignore an incoming verdict field, verify via one ingest cycle that the parent mirror still lands the correct value in the sidecar, then strip the field from the merge set entirely; migrate backfill.py and promotion_sweep.py in the same change since both currently bypass CAS the same way.

5. Estimated effort: 2 engineer-days.

### Zone 4: Config-layer bifurcation: frozen snapshot vs live os.environ

1. Core simplification: Delete the duplicate os.environ re-reads in gates.py, tree_split.py, and indexer.py for values that are already snapshotted into pipeline_config, and make config.py's exported `_envbool` (or an equivalent single truthiness function) the only place any of these env vars are parsed. The frozen snapshot then becomes trustworthy again because nothing downstream can disagree with it.

2. Concrete restructuring steps:
   - config.py: export `_envbool` (rename to `envbool` as public API) as the single truthiness parser used everywhere, replacing ad hoc `.lower() != "true"` and `{'false','0','no','off'}` checks scattered across call sites. (+5 lines, export only)
   - helpers/gates.py:162: replace the direct `os.environ.get("BIDI_COHERENCE_ENFORCE", "true")` re-read with `pipeline_config.bidi_coherence_enforce`, giving the already-defined-but-unused config field (config.py:397/506/705) an actual consumer. (-3/+1 lines)
   - helpers/tree_split.py:382-386: replace the live re-reads of LEAF_CONCENTRATION_PARAGRAPH_SPLIT_ENABLED and LEAF_SPLIT_RATIO with `pipeline_config.leaf_split_ratio`, making the import-time invariant assertion (config.py:597-600, PASS_MAX_LEAF_RATIO <= LEAF_SPLIT_RATIO) actually guard the value in use. (-4/+2 lines)
   - client/indexer.py:529-530: replace the PRE_GARBLE_FORCE_OCR_ENABLED re-read with `pipeline_config.pre_garble_force_ocr_enabled`. (-2/+1 lines)
   - CI: add a lint check (simple grep-based) that flags any `os.environ.get(...)` in a module other than config.py whose var name matches an existing pipeline_config field name, to stop the pattern from recurring. (+~15 lines, one-time script)
   Rough net delta: ~-25/+25 lines net-neutral in the app code, plus a small CI guard that prevents recurrence.

3. Historical bug classes prevented: BIDI_COHERENCE_ENFORCE=1 recording enforce=True in the sidecar while Gate 7 is actually disabled at runtime (differing truthiness sets); the PASS_MAX_LEAF_RATIO <= LEAF_SPLIT_RATIO invariant guarding a value tree_split.py doesn't use; PRE_GARBLE_FORCE_OCR_ENABLED disagreeing between parent and converters_cli child processes when env is mutated mid-run (e.g. in tests).

4. Migration risk: low. Must confirm config.py's module-level snapshot happens after env is finalized in each process (true today since it happens at import time in both server/worker/child processes) — no live-reload use case currently depends on re-reading after startup. Sequence: switch one consumer at a time (gates.py, then tree_split.py, then indexer.py), running the existing gate/split test suites after each; land the CI grep-guard last so it only starts enforcing once real call sites are already clean.

5. Estimated effort: 1 engineer-day.

### Zone 5: Ordered-policy converter chain with load-bearing branch order

1. Core simplification: Fix the RETRY handler so it actually retries (the current `continue` inside a `for idx, entry in enumerate(chain)` loop cannot rewind `idx`, contradicting its own comment) by switching to an explicit `while idx < len(chain)` loop, and extract the branch-order predicate ladder into one small pure classifier function that is unit-tested in isolation — so 'ordering is load-bearing' becomes a property of one tested function instead of an implicit contract across the whole for-loop body.

2. Concrete restructuring steps:
   - client/indexer.py:670-702: convert to `while idx < len(chain):` with the RETRY branch decrementing a per-entry retry counter and re-processing `chain[idx]` without advancing `idx` — matching what the existing comment already claims happens. (-10/+15 lines)
   - client/indexer.py:657-702: extract `_classify_converter_outcome(is_transient, transient_attempts, next_is_agpl, next_idx, chain_len) -> ConverterAction` (RETRY/BLOCK_AGPL/WALK/DONE) as a standalone pure function with its own unit tests covering the full boolean matrix, instead of leaving the classification inline where BLOCK_AGPL's reachability silently depends on loop mechanics. (+30 lines, new pure function + tests)
   - config.py / converters/pipeline.py: add a startup assertion that ALLOW_AGPL_FALLBACK=false also implies AGPL_STRUCTURAL_FALLBACK_ENABLED=false (or merge the two into one named enum: agpl_policy = 'blocked'|'fallback_only'|'structural'), since today they're independent toggles that can be set inconsistently. (+10 lines)
   - helpers/gates.py:227-243: when the D3a pre-garble probe is disabled (ALLOW_AGPL_FALLBACK=false), have Gate 10 explicitly record 'skipped: page_count unavailable' rather than silently never firing, so the licensing toggle's effect on detection quality is visible in the sidecar. (+8 lines)
   Rough net delta: ~-10/+63 lines — the extraction costs lines but removes the licensing-relevant ordering hazard.

3. Historical bug classes prevented: transient converter failures silently falling through to the AGPL converter that BLOCK_AGPL exists specifically to prevent (an HR4 licensing-boundary violation, per CLAUDE.md rule 4); the 'retry' path that never actually retries the same converter; Gate 10 (SUSPECT_DENSITY) silently never firing when ALLOW_AGPL_FALLBACK=false because page_count was never populated.

4. Migration risk: HIGH — this is the AGPL licensing boundary named in CLAUDE.md hard rule 4, so any behavior change must be verified against the full boolean matrix (is_transient x next_is_agpl x retry_exhausted x ALLOW_AGPL_FALLBACK x AGPL_STRUCTURAL_FALLBACK_ENABLED) with a synthetic converter chain before merging, and should not be batched with unrelated zone fixes given the legal exposure. Sequence: write characterization tests for `_classify_converter_outcome` against *current* (buggy) behavior first, then fix the while-loop retry bug behind those tests, re-verify BLOCK_AGPL is actually reachable and enforced, then add the two toggle-consistency and Gate 10 visibility improvements as separate, reviewable commits.

5. Estimated effort: 2-3 engineer-days including full boolean-matrix test coverage and a dedicated AGPL-boundary sign-off.
