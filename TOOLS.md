# TOOLS.md — Claude-Ecosystem Toolset

> **Generated:** 2026-05-30 by `/tool-curator` (autonomous, forked from `/artifact-builder`).
> **Provenance note:** the discovery sub-agent completed its full analysis (phase map, gap analysis,
> Tool Fit Test, conflict resolution, phase scoring) but stalled on the stream watchdog before writing
> this file; the main agent materialized the result via FORK FALLBACK and **demoted picks it could not
> verify in this runtime** (see `## Deferred Discoveries`). Re-run `/tool-curator` to refresh.
>
> **Scope:** tools that aid the *development agent* building this repo — NATIVE / SKILL / MCP / SUBAGENT
> / HOOK / SLASH / CONNECTOR only. The application's own runtime stack is **out of scope** and lives in
> ADRs (`## Routed to ADR`). Edits go through re-running the skill, never by hand (§10A).

---

## Phase Map (from AGENT_DRIVEN_DEVELOPMENT.md §8)

| Phase | Goal | Complexity | Tool cap |
|---|---|---|---|
| 1 | Ingestion + retrieval end-to-end on the GHV corpus (PDF→markdown→tree, store, RAG query) | EXTREME | 10 |
| 2 | All PRD features local: converter fallback chain, `validate_tree` gate, worker hardening, versioning | HIGH | 8 |
| 3 | Wire real externals + production hardening (live OpenAI, real MinIO/Redis, GHCR deploy, dashboards) | HIGH | 8 |

---

## Owned / Native Inventory (already available — not re-registered)

| Kind | Tool | Use |
|---|---|---|
| NATIVE | Read, Write, Edit, Bash, Grep, Glob | File + shell operations |
| NATIVE | WebSearch, WebFetch | External lookups |
| NATIVE (agent) | `Explore`, `Plan` | Read-mostly fan-out search; implementation planning |
| MCP | **codebase-memory-mcp** (`.mcp.json`, http://localhost:8765/mcp) | Structural code graph: callers/callees, data-flow, impact analysis, dead-code — the spine of the TDD `develop` loop |
| SKILL | `dev-core:*` (artifact-builder, -reviewer, -review-fixer, governance-builder, project-bootstrapper, scaffold-installer, tool-curator) | Planning-artifact lifecycle |
| SKILL | `research:deep-research`, `research:skill-scout` | Cited research; skill discovery |
| SKILL | `code-review`, `security-review`, `verify`, `simplify` | Diff review, security audit, behavior verification |

---

## Registered Additions (per phase)

### Phase 1 — Ingestion + retrieval  (cap 10)

| Kind | Tool | Gap it closes | Fit Test |
|---|---|---|---|
| MCP | **codebase-memory-mcp** *(owned)* | Trace call chains / impact before each contract's RED→GREEN; verify layer-isolation imports | ✅✅✅ |
| AGENT | **Explore** *(owned)* | Locate contract IDs, test markers, layer files across the flat package | ✅✅✅ |
| MCP | **GitHub MCP server** (`@modelcontextprotocol/server-github`) | §4.1 commit/PR workflow + CI status reads; `master` is the deploy branch | ✅✅✅ |
| HOOK | **secrets-scan pre-commit hook** (`gitleaks`/`detect-secrets` wrapper in `.claude/hooks/`) | Static gate requires `no_secrets: true`; no owned hook covers it | ✅✅✅ |
| SKILL | `research:deep-research` *(owned)* | Resolve open research items (pymupdf4llm heading suppression, arq DLQ defaults) | ✅✅✅ |

### Phase 2 — Full PRD features local  (cap 8)

| Kind | Tool | Gap it closes | Fit Test |
|---|---|---|---|
| MCP | **codebase-memory-mcp** *(owned)* | Cross-module review for the converter fallback chain + `validate_tree` wiring | ✅✅✅ |
| MCP | **Context7 MCP** (`@upstash/context7-mcp`) | Accurate API docs for pymupdf4llm / Docling / arq / FastMCP at implementation time, without web search | ✅✅✅ |
| SKILL | `research:deep-research` *(owned)* | Docling (MIT) evaluation vs pymupdf4llm (AGPL) — ADR-001 follow-up | ✅✅✅ |

### Phase 3 — Production hardening  (cap 8)

| Kind | Tool | Gap it closes | Fit Test |
|---|---|---|---|
| MCP | **GitHub MCP server** *(Phase 1 carry-over)* | Observe Actions runs, GHCR push, deploy dispatch | ✅✅✅ |
| SKILL | `security-review` *(owned)* | Pre-ship security pass (ZDR routing, erasure cascade, secrets) | ✅✅✅ |
| SKILL | `code-review` *(owned)* | Diff correctness before a red `master` ships an image | ✅✅✅ |

---

## Routed to ADR (out of scope — application runtime / test stack)

These are **not** Claude-ecosystem dev tools; they are the system's own dependencies. They belong in
`ARCHITECTURE.md` ADRs, not here:

- **Runtime:** FastMCP, arq, MinIO, Redis, Prometheus, gunicorn/uvicorn
- **Extraction/RAG:** pymupdf4llm / PyMuPDF *(AGPL — ADR-001)*, Docling *(MIT escape — ADR-001)*, the `pageindex` library, networkx *(ADR-004)*, OpenAI / litellm *(ADR-005 residency)*
- **Test/quality tooling:** pytest, pytest-asyncio, fakeredis, httpx, testcontainers, ruff, mypy, pip-audit

---

## Rejected Candidates

| Candidate | Reason |
|---|---|
| Playwright MCP | No browser UI in scope (PRD non-goal) — Fit Test Q3 fail |
| Postgres MCP | Persistence is MinIO + Redis, not Postgres — Q3 fail |
| Sentry MCP | Runtime observability vendor, not a dev-time agent tool — Q1 fail (→ ADR if adopted) |

---

## Deferred Discoveries

Picks the discovery agent proposed but the main agent could **not verify** in this runtime — revisit
before registering:

- **`pdf` skill** — the discovery agent flagged a PDF-reading gap for the `issue/data/` corpus, but the
  native **Read** tool already renders PDFs (via its `pages` parameter). A *dedicated extraction* skill
  may still help validate tree quality (FR-0.7); confirm one exists and beats native Read before adopting.
- **`dev-implement:ticket-implementer`, `dev-implement:swarm-builder`** — claimed in the owned-skill
  inventory but **not present** in this runtime's skill registry. If/when installed, they map cleanly onto
  the per-contract `develop` loop and `phase_features` parallel groups respectively; revisit then.
- **GitHub MCP server / Context7 MCP** — registered above on reputation; verify the server binaries are
  installed and authenticated (`gh` auth / Upstash key) at first use.

---

## History

| Date | Change |
|---|---|
| 2026-05-30 | Initial curation via `/artifact-builder` fork. 5 Phase-1 / 3 Phase-2 / 3 Phase-3 tools assigned (most owned). 3 candidates rejected; 11 deps routed to ADR; 3 discoveries deferred. Produced via FORK FALLBACK after the discovery sub-agent stalled pre-write. |
