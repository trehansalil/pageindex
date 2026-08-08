# Task 9.5 / RFC-034 D12: Stale-Window Doc Re-ingestion — Status

## Status: NOT EXECUTED — blocked on unmet prerequisites, no remote infra route from this sandbox

RFC-034 D12 and Task 9.5 require, in order, before re-ingesting the German
table-heavy subset (GHV-TKV-Tarif, Unfallversicherung, Haftpflicht,
world-stats-pocketbook):

1. **D0-D2 confirmed-fresh remote deploy** — not confirmed from this sandbox.
2. **D2.5 pre-redeploy separator-count baseline captured** (Task 1.4) — per
   `audit/TABLE_SEPARATOR_BASELINE_2026-08-08.md`, this is explicitly
   **NOT YET CAPTURED**: the script exists and is lint-clean, but could not
   reach the remote MinIO bucket from any sandbox tried so far.
3. **D5 provenance fields in place** (Task 5.3) — this part is landed:
   `src/pageindex_mcp/storage.py:416` has `SIDECAR_VERSION = 3` and the sidecar
   write path populates it (`storage.py:531`), so `converter_name` /
   `extraction_route` provenance fields exist on the write side.

Prerequisites 1 and 2 are unmet, so per the RFC's own sequencing rule
("Prerequisite: D2.5's pre-redeploy separator-count baseline captured before
re-ingestion, otherwise before/after delta cannot be measured"), re-ingesting
now would destroy the only chance to measure the D12 before/after table-repair
delta. Re-ingestion was deliberately **not performed** in this session.

## What was verified in this session

- `make env-remote` was run to check for a route to the remote k3s `infra`
  namespace. It failed the same way the D2.5 baseline attempt already
  documented: `kubectl` has no live context (`connection refused` to
  `127.0.0.1:<port>`, the local port-forward is not up), so
  `scripts/make_remote_env.sh` cannot resolve `svc/minio` and `env/remote.env`
  is never written.
- `env/` contains only `local.env` (docker-compose defaults) — no
  `remote.env`.
- No Scaleway Docling endpoint or credentials are reachable from this sandbox
  either, so even a manual re-ingest against the "confirmed-fresh remote
  route" required by D12 is not possible here.

## What is required to actually execute this task

From a host with real `kubectl` access to the `infra` k3s namespace:

```bash
make env-remote                        # writes env/remote.env
set -a && source env/remote.env && set +a
uv run python scripts/table_separator_baseline.py   # D2.5 — MUST run first
# then, only after D0-D2 fresh deploy is independently confirmed:
uv run python preprocess_client.py "GHV-TKV-Tarif*"
uv run python preprocess_client.py "Unfallversicherung*"
uv run python preprocess_client.py "Haftpflicht*"
uv run python preprocess_client.py "world-stats-pocketbook*"
```

Then compare each doc's post-ingest `meta.json` (`converter_name`,
`extraction_route`, `total_tree_chars`, node counts, table separator style)
against:
- its Run 15 baseline in `audit/CORPUS_REINGESTION_AUDIT_RUN-15.md`, and
- its row in `audit/TABLE_SEPARATOR_BASELINE_2026-08-08.md` (once that file
  holds real counts, not the "NOT YET CAPTURED" placeholder).

Docs still MARGINAL after re-ingestion have code defects, not stale-build
defects — feed that into the C5/C6 cluster assessment in
`audit/RECONCILIATION_REPORT.md`.

## No code changes

Per RFC-034 D12 / Task 9.5, this is an operational/validation step only. No
files under `src/`, `services/`, or `tests/` were touched in this session.
