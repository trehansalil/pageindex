# Pending Decisions & Changes

Items here are not yet integrated into living documents.
Move to Resolved with date + integration target when handled.

> **Stage status (2026-05-31, RFC-003).** The two drift `[AMENDMENT]`s and the
> `eval.sh`/`contracts.sh`/`ANALYSIS.md` hygiene items are **resolved** below. The
> remaining `[FIX]`es all have a **locked decision** (RFC-003 §Decisions) but await
> **Stage 2 source code** — they land *after* the governance checkpoint, so they stay
> `Unresolved` until the code is written. The 3 `[DECISION]`s remain human-owned/standing.

## Tag Format
- [FIX]       Bug or defect — no spec change
- [GAP]       Spec was silent; implementation chose
- [AMENDMENT] Existing spec decision must change
- [DECISION]  Open question needing human judgment

Format: `- [TAG] YYYY-MM-DD | Description`

## Unresolved

All items below have a **locked decision** (RFC-003 §Decisions, tagged D1–D5) and are
**Stage 2 source-code work** — they remain `Unresolved` only because the code is not yet
written. They land after the governance checkpoint. The decision is no longer open; the
implementation is.

### Stage 2 — code (decisions locked in RFC-003; pending checkpoint)
- [FIX] 2026-05-31 | **CACHE-01 read-through (D1: refactor code).** Move the read-through out of storage.load_doc (storage.py:13/49-61) and into cache.get; make storage.load_doc cache-unaware; keep storage→cache *invalidation* (CACHE-01-C2) behind a **function-local (lazy) import** to stay acyclic; flip dag.yaml `storage:[cache]`→`cache:[storage]` **with the code**; repoint cached read callers (tools/documents.py:14) to cache.get. CACHE-01.yaml unchanged (already the target). → RFC-003 D1 / Amdt 1.
- [FIX] 2026-05-31 | **HR2 delete_doc (D5).** Rewrite storage.py:98-114 to the mandated order uploads/<id>/ → processed/<id>.json → processed/<id>.meta.json → Redis pageindex:doc:<id> (each idempotent), then clear the processed_hashes.json hash-cache entry (else a re-upload dedups to a tombstoned doc_id). ERASE-01 is the contract of record. HIGH.
- [FIX] 2026-05-31 | **HR5 validate_tree (D5).** Add validate_tree() and call it before save_doc (client.py:147 is unconditional); **validate_tree BLOCKS at runtime** — when a tree fails, save_doc is NOT called, the tree is discarded, the worker sets job status=error with reason=low_quality_tree, and pageindex_low_quality_trees_total{reason} is incremented. The term "warn-only" applies ONLY to the CI posture: the depth<2 / node_count<3 thresholds are not a hard CI gate (they can be tuned), but runtime ALWAYS blocks persistence of a failing tree. Backs WORKER-01-C2 / INDEX-01-C3. Threshold promotion to a hard CI gate stays deferred (Standing). HIGH.
- [FIX] 2026-05-31 | **INDEX-01 Docling PDF route (D3).** Build the markdown-first PDF path (client.py:94-96 still routes .pdf → _run_page_index/PyPDF2): **Docling (MIT) primary, pymupdf4llm fallback**, but validate Docling against issue/data/ (GHV T&C corpus) **before wiring**; try/except primary→fallback; no_pypdf2_in_new_pdf_path static gate applies. Docling-primary is also the HR4 AGPL escape (narrows the §13 gate). HIGH.
- [FIX] 2026-05-31 | **WORKER-01 lifecycle (D4).** worker.py never sets status=processing (pending→done/error only, :54/60); WorkerSettings (:83-87) has no retry/timeout/DLQ. Add status=processing on start + bounded max_tries + job_timeout + push to pageindex:dlq on final failure. Satisfies WORKER-01-C1/C3.
- [FIX] 2026-05-31 | **RAG-01-C3 empty-corpus shape (D5).** find_relevant_documents returns a bare string on the no-docs branch (tools/documents.py:79-81); return the query_error_shape JSON envelope with available=[] and increment pageindex_tool_errors_total{tool=find_relevant_documents}.
- [FIX] 2026-05-31 | **CONV-01-C2 dash normalization (D5).** No Unicode-dash normalizer in converters.py/client.py/helpers.py. Add one (U+2013 en-dash, U+2014 em-dash, U+2212 minus → ASCII '-'; NFKC) applied at **both** extraction and query time; clause-code matching on the German T&C corpus depends on it.
- [FIX] 2026-05-31 | **Gate 3 test IDs (D5).** contracts.sh is now functional (see Resolved) and correctly FAILs: none of the 24 *-01-C* IDs appear in tests/. Add each Phase-1 contract ID to a test name/marker/docstring (§5.3) to turn the gate green.

### Deferred / standing (human-owned; not blocking)
- [DECISION] 2026-05-31 | Promote validate_tree thresholds: after calibration vs the GHV corpus + a clean control set, add node_count/depth/garbling thresholds to verify-gates.yaml and flip the gate warn-only→error via a Phase-2 RFC (Hard Rule 5; re-seed of RFC-001 action item E).
- [DECISION] 2026-05-31 | (deploy-time) AGPL §13 legal sign-off before serving PyMuPDF/pymupdf4llm over a network externally — or an Artifex license. **Narrowed by RFC-003 D3:** Docling (MIT) primary removes AGPL from the default PDF path, leaving it only on the pymupdf4llm fallback (R10 / Hard Rule 4 / RFC-000).
- [DECISION] 2026-05-31 | (deploy-time) Pick a no-training + ZDR + EU-residency LLM tier per deployment via OPENAI_BASE_URL; self-hosted is the ultimate fallback (R9 / Hard Rule 3 / RFC-000).

## Resolved
- **2026-05-31 | RAG-01 transport-bypass [AMENDMENT] (RFC-002 Amdt 1)**: decision locked (RFC-003 D2) = **refactor the contract**, and **applied**. rag-01.yaml `module: client` → `helpers`, with the transport(server: tools/documents.py) → helpers → storage(repo dep) span documented in-file; never imports client (verified tools/documents.py:7,14, find_relevant_documents :68-90). `server` can't be a contract module (contracts.sh §3b). Contract effects C1/C2/C3 unchanged (C3 stays a code target → Stage 2).
  → Integrated into: .agents/contracts/rag-01.yaml, RFC-003 §D2 / Amendment 2 (2026-05-31)
- **2026-05-31 | CACHE-01 storage↔cache read-through direction [AMENDMENT] (RFC-002 Amdt 1)**: decision locked (RFC-003 D1) = **refactor the code** (read-through moves into cache.get; structural edge becomes cache→storage; invalidation back-edge lazy-imported to stay acyclic; dag flip + repoint happen in Stage 2 with the code). CACHE-01.yaml unchanged (already the target). Decision closed; **implementation pending** — re-seeded above as a Stage-2 [FIX].
  → Integrated into: RFC-003 §D1 / Amendment 1 (2026-05-31)
- **2026-05-31 | Gate 3 contracts.sh non-functional (UPGRADED from "no contract IDs in tests")**: FIXED. Four `set -euo pipefail` no-match-grep aborts repaired (§3b server-module check :81; §3c line-118 ID regex missing `- id:`; + 2 masked: BSD `grep -c`, per-ID tests/ grep under pipefail). Gate now runs end-to-end, extracts all 24 IDs, and correctly FAILs (PASS=6 FAIL=29 WARN=11). The real coverage gap (add IDs to tests) is re-seeded above as a Stage-2 [FIX].
  → Integrated into: scripts/gates/contracts.sh, RFC-003 §Fixes (2026-05-31)
- **2026-05-31 | eval.sh "breaks before the summary loop" (cosmetic) [FIX]**: **REFUTED.** eval.sh:184 `break` exits only the gate `for` loop, not the script; the summary table (:200-215) + final verdict (:222-232) still run (per the :183 comment), un-run gates render `skipped` via the `:-skipped` default (:201), exit code stays correct. Working-as-designed; no change.
  → Integrated into: RFC-003 §Context (REFUTED), verification S296 (2026-05-31)
- **2026-05-31 | Dangling issue/ANALYSIS.md references**: cleaned. Ground-truth verified the file does **not** exist on disk (only issue/data/*.pdf). 12 cites redirected to RFC-000 across AGENT_DRIVEN_DEVELOPMENT.md (9), vocabulary.yaml, develop-guide.yaml, verify-gates.yaml. RFC-002's 2 cites left as historical prose (closed/append-only).
  → Integrated into: AGENT_DRIVEN_DEVELOPMENT.md, .agents/governance/{vocabulary,develop-guide,verify-gates}.yaml, RFC-003 §Fixes (2026-05-31)
- **2026-05-31 | .pdf routes through PyPDF2+LLM-TOC; should go PDF→markdown→md_to_tree (RC1/RC3/RC4)**: DECISION closed (frozen in RFC-000); behavior owned by INDEX-01 + CONV-01; static gate `no_pypdf2_in_new_pdf_path`. NOTE: implementation NOT yet written — re-seeded above as an INDEX-01 [FIX] (client.py:94-96 still PyPDF2).
  → Integrated into: RFC-000 §Resolved Ambiguities, .agents/contracts/index-01.yaml, conv-01.yaml; re-affirmed in RFC-002 (2026-05-31)
- **2026-05-31 | client.index() persists empty/garbled trees silently; add validate_tree() before save_doc**: DECISION closed, owned by WORKER-01-C2 (+ STORE-01-C1); validate_tree BLOCKS at runtime (save_doc NOT called on failure; job status=error with reason=low_quality_tree; pageindex_low_quality_trees_total{reason} incremented). "Warn-only" applies ONLY to the CI threshold posture (depth<2 / node_count<3 thresholds are tunable, not a hard CI gate), NOT to runtime blocking. NOTE: validate_tree NOT yet in code — re-seeded above as an HR5 [FIX].
  → Integrated into: RFC-000 §Resolved Ambiguities, .agents/contracts/worker-01.yaml, store-01.yaml; re-affirmed in RFC-002 (2026-05-31)
- **2026-05-31 | upload.py targets unregistered process_document tool; CLAUDE.md tool claims stale (RC6)**: canonical entrypoint is POST /upload/files → arq enqueue (UPLOAD-01); CLAUDE.md already states upload.py is not an active MCP tool.
  → Integrated into: RFC-000 §Resolved Ambiguities, .agents/contracts/upload-01.yaml, CLAUDE.md; re-affirmed in RFC-002 (2026-05-31)
- **2026-05-31 | dag.yaml#bootstrap.scaffold.check.paths lists src/modules/ but repo is flat src/pageindex_mcp/**: corrected the check.paths entry + artifact label.
  → Integrated into: .agents/governance/dag.yaml (scaffold node), RFC-002 §Fixes (2026-05-31)
- **2026-05-31 | CI (build-push.yml) does not run a test job executing scripts/eval.sh to gate build-push**: added a `test` job running `scripts/eval.sh --no-infra` (gates 1–6); build-push now `needs: test`; deploy trigger unchanged.
  → Integrated into: .github/workflows/build-push.yml, RFC-002 §Fixes (2026-05-31)
- **2026-05-31 | dag.yaml#phase_features.modules is [] and must be populated (RFC-001 action A)**: applied the 8 module nodes with code-accurate edges (supersedes RFC-000's declared edges AND RFC-001 finding 11's "edges complete" verdict where the implementation diverged: cache:[], storage:[cache], client:[storage,converters], upload_app:[storage,client], server:[storage,upload_app]).
  → Integrated into: .agents/governance/dag.yaml#phase_features.modules, RFC-002 Amendment 1 (2026-05-31)
- **2026-05-31 | Add ERASE-01 (Phase-2) erasure cascade owned by storage (RFC-001 action C / Hard Rule 2)**: derived the contract (uploads/ → processed/*.json → processed/*.meta.json → Redis cache → clear hash-cache; backups out-of-scope for the testable effect; idempotent). NOTE: existing delete_doc violates this order — re-seeded above as an HR2 [FIX].
  → Integrated into: .agents/contracts/erase-01.yaml, RFC-002 Amendment 2 (2026-05-31)
- **2026-05-31 | Name the arq-enqueue mock boundary backing UPLOAD-01's offline proof (RFC-001 action D)**: JobEnqueuer cited by UPLOAD-01-C1 + recorded in RFC-001 Amendment 2 — closed in spec. Code still calls arq.create_pool inline (upload_app.py:12); wiring the interface is a UPLOAD-01 tidy-up, not a spec change.
  → Integrated into: .agents/contracts/upload-01.yaml, RFC-001 Amendment 2, RFC-002 Amendment 3 (2026-05-31)
- **2026-05-31 | Confirm whether server touches storage/cache directly or only via client (RFC-001 action F)**: from code — server (server.py + tools/documents.py) imports storage directly, NOT client or cache; DAG edge set to `server:[storage, upload_app]`.
  → Integrated into: .agents/governance/dag.yaml, RFC-002 Amendment 1 (2026-05-31)
- **2026-05-31 | Two governance-tooling bugs in dag.sh**: (1) nodes_resolve (4b) AND the exec-order disk fallback (4c) read singular check.path while the yaml uses check.paths → checks no-op'd for every node; now both honor paths + type file|dir|glob+min_matches. (2) derived: never actually rewritten despite the claim; added `dag.sh --write` marker-splice regeneration (plain run stays read-only). Gate now PASS=19 FAIL=0.
  → Integrated into: scripts/gates/dag.sh, .agents/governance/dag.yaml header, AGENT_DRIVEN_DEVELOPMENT.md §4.3, RFC-002 §Fixes (2026-05-31)
