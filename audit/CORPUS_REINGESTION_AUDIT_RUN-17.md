<!-- Space: CITRA -->
<!-- Title: Corpus Re-ingestion Audit — Run 17 -->
<!-- Folder: Audits -->

# Corpus Re-ingestion Audit — Run 17

## Status: BLOCKED — no reachable ingestion infrastructure from this execution environment

## Environment

- Branch: feat/pdf-inspector-shadow-pilot
- Date: 2026-08-09
- Prior run: `audit/CORPUS_REINGESTION_AUDIT_RUN-16.md` (`audit/REGRESSION_WATCHDOG_RUN-16.md`)
- Task: `.agents/tasks/tasks-rfc034-run15-reconciliation-remediation.md` [15.1](tasks-rfc034-run15-reconciliation-remediation.md#task-15-1) — full 25-doc ingest+score cycle validating D16-D21

---

## What was verified

### 1. D16-D21 are landed in code (all six confirmed present on `HEAD` = `6484f1f`)

| Decision | Evidence |
|---|---|
| D16 (ToC-strip depth guard) | `src/pageindex_mcp/helpers.py:2751` `_strip_toc_heading_nodes_guarded()` — all-or-nothing guard, discards the strip if depth drops >1 or >20% of nodes are removed |
| D17 (MOU/bilingual block-merging) | `src/pageindex_mcp/converters.py:2677` — mixed-script (Arabic+Latin) row guard in table repair |
| D18 (write-visibility barrier) | `src/pageindex_mcp/storage.py:28-39` — 4-attempt read-after-write consistency check after `put_object`, same backoff schedule as RFC-033 D3 |
| D19 (enrichment content preservation) | landed per Batch 7 task 13.7/13.8 (char-density comparison in enrichment promotion) |
| D20 (marsoom 13 depth investigation) | closed via D16's guard per task 13.9 |
| D21 (D2 Part B scoped Arabic re-ingest gate) | recorded in `audit/RFC033_D2_PARTB_TASK_9_1_GATE_2026-08-09.md` |

Gate G7 (Batch 7 complete) and tasks 13.1-13.10 are checked off in the tasks file, consistent with the code state above.

### 2. Ingestion infrastructure is not reachable from this session

```
$ make preflight
ClusterIP is unreachable and PI_REMOTE_MINIO_PUBLIC_ENDPOINT is unset in env/remote.env — no usable MinIO address
make: *** [env] Error 2
```

- `env/remote.env` does not exist in this checkout (gitignored; generated only by `make env-remote`, which requires `kubectl` against the live k3s node).
- `kubectl config current-context` resolves to `docker-desktop` (a local kind cluster), not the remote k3s node that hosts the corpus's MinIO/Redis/Postgres/Docling.
- The remote k3s ClusterIP (`10.43.x.x:9000`) is unroutable from here (`nc` connect refused/timeout), and no `PI_REMOTE_MINIO_PUBLIC_ENDPOINT` fallback is configured.
- `.env` carries only local MinIO defaults (`localhost:9000` / `minioadmin`) — no remote MinIO credentials are present anywhere in this checkout to construct a manual override.
- The Scaleway remote Docling function endpoint (`DOCLING_SERVICE_URL` in `.env`) did not respond within 10s (cold-start function, unverifiable without a longer probe and without MinIO reachable there is no ingest target regardless).
- Local docker-compose infra (`pageindex-minio`, `pageindex-redis`, `pageindex-postgres`, `pageindex-docling-service`) is running, but this is **not** the corpus store — the persisted 25-doc corpus and its Run 1-16 history live exclusively in the remote MinIO bucket that Runs 6-16 scored against. A local-profile run would ingest `doc_store/` into an empty bucket and produce numbers that are not comparable to prior runs' `PASS`/`MARGINAL`/`FAIL` deltas (R1-R6), and the local Docling container is a stale 2026-07-30 build predating D0-D21 — it would not exercise the actual production conversion path this RFC's redeploy gate (D2) validated.

### 3. No corpus run was executed

Per the "Fabricated corpus report" lesson (`audit/RECONCILIATION_REPORT.md` history), **no scorecard, tallies, or per-document verdicts are fabricated or estimated in this report.** Task 15.1 is an operational validation step that requires live access to the remote MinIO/Redis/Postgres/Docling stack this repository's `PROFILE=remote` targets — access this execution environment does not have.

---

## Disposition

- **Task 15.1 is NOT complete.** No corpus cycle ran; R1-R6 status is unchanged from `REGRESSION_WATCHDOG_RUN-16.md`.
- **Task 15.1 checkbox left unchecked** in `.agents/tasks/tasks-rfc034-run15-reconciliation-remediation.md` — not flipped by this report, since the acceptance criteria (regressions resolved/improved) cannot be evaluated without a real run.
- **Gate G8** cannot close until a session with one of the following runs this task:
  1. `kubectl` access to the k3s node to regenerate `env/remote.env` (`make env-remote`), then `make preflight && make ingest && <score pipeline>` against the real corpus per the `corpus-ingest-score` skill; or
  2. A manually provisioned `env/remote.env` with the production MinIO/Redis/Postgres endpoints and credentials, copied from a host that has them (per `docs/ENV_PROFILES.md` § "Remote MinIO from anywhere").

## Recommended next step

Re-dispatch task 15.1 from a session running on, or with `kubectl` access to, the k3s host (the same class of environment that produced Runs 6-16), so `make env-remote` can regenerate `env/remote.env` before `make preflight` / `make ingest` / scoring proceed.
