# Pending Decisions & Changes

Items here are not yet integrated into living documents.
Move to Resolved with date + integration target when handled.

> **Stage status (2026-05-31, RFC-003 — Stage 2 COMPLETE).** Stage 1 (governance/docs)
> and Stage 2 (Tier-0 source code) are both **done and verified**: contracts gate PASS=35/0,
> dag gate PASS=19/0, import smoke clean (no circular import), pytest 69 passed / 2
> pre-existing env failures (redis-default, prometheus `/proc`) / 1 skipped, all 3
> adversarial HR/contract reviews compliant. The 8 Stage-2 `[FIX]`es moved to **Resolved**
> (see the "Stage-2 implementation" block). Only the 3 human-owned `[DECISION]`s remain standing.

## Tag Format
- [FIX]       Bug or defect — no spec change
- [GAP]       Spec was silent; implementation chose
- [AMENDMENT] Existing spec decision must change
- [DECISION]  Open question needing human judgment

Format: `- [TAG] YYYY-MM-DD | Description`

## Unresolved

The Stage-2 source-code `[FIX]`es that lived here are **all implemented and verified** —
moved to **Resolved** (see the "Stage-2 implementation" block). What remains is only the
human-owned standing work below; no code decision is open.

### Deferred / standing (human-owned; not blocking)
- [DECISION] 2026-05-31 | Promote validate_tree thresholds: after calibration vs the GHV corpus + a clean control set, add node_count/depth/garbling thresholds to verify-gates.yaml and flip the gate warn-only→error via a Phase-2 RFC (Hard Rule 5; re-seed of RFC-001 action item E).
- [DECISION] 2026-05-31 | (deploy-time) AGPL §13 legal sign-off before serving PyMuPDF/pymupdf4llm over a network externally — or an Artifex license. **Open by default, narrowable on demand (RFC-003 Amendment 4 supersedes Amendment 3):** the NO-GO was MPS-only — Docling-CPU (MIT) is now validated on Apple Silicon and is one env-flag away (`PDF_CONVERTER=docling`), so a deployment can remove AGPL `pymupdf4llm` from the PDF path without a code change. The default path is still AGPL, so the §13 gate is open unless that flag is set; network-serving sign-off stays owner-owned (R10 / Hard Rule 4 / RFC-000).
- [DECISION] 2026-05-31 | (deploy-time) Pick a no-training + ZDR + EU-residency LLM tier per deployment via OPENAI_BASE_URL; self-hosted is the ultimate fallback (R9 / Hard Rule 3 / RFC-000).

## Resolved

### Stage-2 implementation (2026-05-31, RFC-003 D1–D5 — verified: contracts PASS=35/0, dag PASS=19/0, pytest 69 passed, 3 adversarial reviews compliant)
- **CACHE-01 read-through (D1)**: read-through moved to `cache.get_doc` (cache.py:64, lazy `from .storage import load_doc` on miss); `storage.load_doc` made cache-unaware pure-MinIO read (storage.py:46, response-unbound bug fixed); invalidation kept via lazy import in `save_doc`/`delete_doc` (storage.py:89,145); dag edge flipped to `cache:[storage]` / `storage:[]` (dag.yaml via `dag.sh --write`); read callers repointed to `get_doc` (client.py:199/215/227; tools/documents.py:15,47/102/140/164). Tests: CACHE-01-C1/C2/C3.
  → src/pageindex_mcp/{cache,storage,client}.py, tools/documents.py, .agents/governance/dag.yaml, tests/test_cache_contract.py
- **HR2 delete_doc (D5 / ERASE-01)**: rewritten to the mandated cascade (storage.py:95) — uploads/<id>/* → processed/<id>.json → .meta.json → Redis → hash-cache (doc_name captured up-front for step 5); idempotent (NoSuchKey tolerated); partial failure raises naming the failing store. Tests: ERASE-01-C1/C2/C3.
  → src/pageindex_mcp/storage.py, tests/test_storage_contract.py
- **HR5 validate_tree (D5)**: `validate_tree` + `LowQualityTreeError` added (helpers.py:261+); gate runs in `client.index` BEFORE doc_id/save (client.py:148-153) — failing tree raises, nothing persists; `LOW_QUALITY_TREES{reason}` incremented; worker maps it to status=error/reason=low_quality_tree, terminal, no DLQ, no re-raise (worker.py:66). Runtime BLOCKS (the "warn-only" label scopes only to the tunable CI thresholds). Tests: validate_tree branches + WORKER-01-C2.
  → src/pageindex_mcp/{helpers,client,worker,metrics}.py, tests/test_validate_tree_contract.py
- **INDEX-01 PDF route (D3 → Amendments 3 & 4)**: implemented **pymupdf4llm-primary + page_index fallback** (`converters.pdf_to_markdown`, converters.py:34, lazy `import pymupdf4llm`, relevel headings + dash-normalize → temp .md → `_run_md_to_tree`; all-converters-fail → `_run_page_index`). **Amendment 4 (re-validation 2026-05-31):** the NO-GO was MPS-only — Docling-CPU works on Apple Silicon (force `cpu` on darwin) and is now a **config-gated converter**: `converters.pdf_to_markdown_docling` + `converters.pdf_markdown_converters()` ordered by `PDF_CONVERTER` env (client.py iterates the chain). Head-to-head (`scripts/docling_spike_compare.py`) showed pymupdf4llm **drops the `ﬂ` ligature** (Haftpflicht→Haftpficht, 8-14×/doc) which Docling fixes (0 corruptions) — HR5 `validate_tree` does NOT catch this. **Update 2026-05-31 (user decision): Docling is now the DEFAULT primary + a CORE dependency** — `PDF_CONVERTER` defaults to `docling`, `docling>=2.96.0` moved into core `dependencies` (locked in `uv.lock`); base install + CI now pull `torch`. CPU-only unconditional (`AcceleratorDevice.CPU`). pymupdf4llm stays core as the secondary/fallback; `PDF_CONVERTER=pymupdf4llm` reverts the primary. Tesseract `deu` installed at repo-local `.tessdata/` (`TESSDATA_PREFIX`). Tests green (69 passed). PyPDF2 stays out of the new route. **HR4: AGPL no longer default-primary; fully closable by dropping pymupdf4llm.** Tests: INDEX-01-C1/C2/C3.
  → src/pageindex_mcp/{converters,client,metrics}.py, pyproject.toml, .agents/rfcs/003-tier0-decision-lock.md (Amendment 3), tests/test_converters_contract.py
- **WORKER-01 lifecycle (D4)**: status=processing set first inside the try (worker.py:53); `MAX_TRIES=2` / `JOB_TIMEOUT=900` / `DLQ_KEY="pageindex:dlq"` (worker.py:27); generic except → push to DLQ on `job_try>=MAX_TRIES` then re-raise for arq retry (worker.py:72); `WorkerSettings.max_tries`/`job_timeout` wired. Tests: WORKER-01-C1/C2/C3.
  → src/pageindex_mcp/worker.py, tests/test_worker_contract.py
- **RAG-01-C3 empty-corpus shape (D5)**: no-docs branch now returns `json.dumps({"error": ..., "available": []})` + `TOOL_ERRORS{tool=find_relevant_documents}` increment (tools/documents.py:81-83). Tests: RAG-01-C3.
  → src/pageindex_mcp/tools/documents.py, tests/test_rag_contract.py
- **CONV-01-C2 dash normalization (D5)**: `normalize_dashes` (U+2013/2014/2212 → '-') in converters.py:14; applied at extraction (docx/pptx/html returns + pdf route) and at query time (`helpers._rag` first line, helpers.py:115). Tests: CONV-01-C2.
  → src/pageindex_mcp/{converters,helpers}.py, tests/test_converters_contract.py
- **Gate 3 test IDs (D5)**: all 24 `*-01-C*` contract IDs now appear in tests/ with real behavior assertions; contracts gate PASS=35 FAIL=0 (was PASS=6 FAIL=29).
  → tests/test_{cache,storage,validate_tree,converters,rag,upload,worker}_contract.py

### Stage-1 governance / earlier
- **2026-05-31 | RAG-01 transport-bypass [AMENDMENT] (RFC-002 Amdt 1)**: decision locked (RFC-003 D2) = **refactor the contract**, and **applied**. rag-01.yaml `module: client` → `helpers`, with the transport(server: tools/documents.py) → helpers → storage(repo dep) span documented in-file; never imports client (verified tools/documents.py:7,14, find_relevant_documents :68-90). `server` can't be a contract module (contracts.sh §3b). Contract effects C1/C2/C3 unchanged (C3 stays a code target → Stage 2).
  → Integrated into: .agents/contracts/rag-01.yaml, RFC-003 §D2 / Amendment 2 (2026-05-31)
- **2026-05-31 | CACHE-01 storage↔cache read-through direction [AMENDMENT] (RFC-002 Amdt 1)**: decision locked (RFC-003 D1) = **refactor the code** (read-through moves into cache.get; structural edge becomes cache→storage; invalidation back-edge lazy-imported to stay acyclic; dag flip + repoint happen in Stage 2 with the code). CACHE-01.yaml unchanged (already the target). Decision closed; **implementation done** (Stage 2 — see the Stage-2 implementation block).
  → Integrated into: RFC-003 §D1 / Amendment 1, src/pageindex_mcp/{cache,storage,client}.py, tools/documents.py (2026-05-31)
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
