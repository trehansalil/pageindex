---
zone_name: Config Snapshot vs Live-Read Divergence
severity: medium
bug_count: 2
status: improved
audit_date: 2026-09-01
audit_run: POST-RFC041
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-01_POST-RFC041.md
key_files:
  - src/pageindex_mcp/config.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/converters/pictures.py
tags:
  - zone-spec
  - medium
  - config
  - dual-source
scorecard_verdict: regressed
scorecard_date: 2026-09-01
scorecard_run: POST-RFC041
---
## Mechanism

config.py builds a frozen PipelineConfig dataclass at import time from 88 environment variable reads, while 9 other source files contain 121 os.environ references that bypass the frozen snapshot. Boolean flags use different truthiness parsing between the snapshot and live reads, so the same configuration variable can evaluate to different values depending on which access path the code takes.

1. **Dual-source configuration truth:** PipelineConfig freezes thresholds at import time for sidecar auditability, but modules like gates.py, tree_split.py, and indexer.py read `os.environ` at call time, so runtime env-var changes affect some code paths but not others.

2. **Boolean parsing divergence:** `BIDI_COHERENCE_ENFORCE=1` records `enforce=True` in sidecar while gates.py exact-match might disable the gate (Chain 7).

3. **Config consolidation reveals drift:** Commit 610d078 revealed `DEPTH_ADEQUACY_FLOOR` and `CHAR_FLOOR` had drifted 1-2 units between call sites, flipping verdicts for ~20 documents misdiagnosed as extraction regression. Future threshold consolidation will similarly flip borderline verdicts and get misdiagnosed.

## History

- **Chain 7:** Zone-7 config-layering fix revealed `DEPTH_ADEQUACY_FLOOR`/`CHAR_FLOOR` drifted 1-2 units between call sites, changing verdict outcomes for ~20 documents misattributed to extraction regression.
- **Chain 18:** RFC-031 cache-bypass flag fixed only one gating instance; same write-once/never-invalidated caching pattern recurs in Redis cache and MinIO etag reconciliation.

## Code Evidence

1. **os.environ live reads:** Search across src/ returns 121 matches in 9 files: garble.py, subprocess_mgr.py, pictures.py, gates.py, llm.py, tracing.py, config.py, indexer.py, converters_cli.py — live reads persist outside frozen PipelineConfig.

2. **PipelineConfig** at config.py:366-578 is frozen dataclass with 88+ field definitions.

3. **Config reset function:** reset_pipeline_config at config.py:626-669 includes `ALLOW_AGPL_FALLBACK` confirming dual sourcing.

## Key Files

| File | Role |
|------|------|
| config.py:366-578 | PipelineConfig frozen dataclass with 88+ fields |
| config.py:626-669 | reset_pipeline_config with dual-source confirmation |
| gates.py | Live os.environ reads bypassing frozen config |
| indexer.py | Live os.environ reads in indexing logic |
| pictures.py | Live os.environ reads in picture processing |
| subprocess_mgr.py | Live os.environ reads in subprocess management |
