# Table-Separator Baseline (2026-08-08)

Pre-redeploy read-only probe, RFC-034 D2.5. Window: 2026-07-30..2026-08-04.

## Status: NOT YET CAPTURED — script unable to reach remote MinIO from this sandbox

`scripts/table_separator_baseline.py` is written and ready (read-only, no MinIO
writes; verified against local profile connection logic and lint-clean). This
sandbox has no route to the remote k3s `infra` namespace (`kubectl` has no
current-context server, `env/remote.env` does not exist yet, and local MinIO
at `localhost:9000` is not running here), so the script could not be executed
against the actual remote-hosted `processed/` bucket to populate real counts.

**This MUST be run for real, from an environment with remote MinIO access,
before Task 1.5 triggers the redeploy** — once D2's redeploy lands and docs
are re-ingested (D12), the pre-redeploy state is destroyed and this baseline
becomes unmeasurable.

## How to run it for real

```bash
make env-remote            # on a host with kubectl access to the infra namespace
                            # writes env/remote.env
set -a && source env/remote.env && set +a
uv run python scripts/table_separator_baseline.py
```

This overwrites this file with the actual per-doc separator counts table
(`doc_id | doc_name | processed_at | unrepaired |----| | repaired | --- |`)
for every `processed/*.json` tree whose `.meta.json` `processed_at` falls in
2026-07-30..2026-08-04, plus a total doc count. The script performs only
`list_objects`/`get_object` reads — no MinIO writes.
