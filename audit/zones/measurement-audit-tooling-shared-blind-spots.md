---
zone_name: Measurement / Audit Tooling Shared Blind Spots
severity: high
bug_count: 3
status: new
audit_date: 2026-08-27
audit_run: POST-RUN20
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-27_POST-RUN20.md
key_files:
  - src/pageindex_mcp/storage/verdict.py
  - src/pageindex_mcp/helpers/verdict.py
  - src/pageindex_mcp/helpers/garble.py
  - src/pageindex_mcp/worker/registry_mirror.py
tags:
  - zone-spec
  - high
  - measurement
  - audit
  - tooling
scorecard_verdict: needs_another_cycle
scorecard_date: 2026-08-27
scorecard_run: POST-RUN20
wave: 3
---
## Mechanism

The diagnostic and measurement tooling used to audit corpus quality inherits the same structural blind spots as the pipeline it measures, or has its own process-integrity bugs that produce misleading data. Char-count scoring via block.get('text','') misses table blocks entirely because they carry content in headers/rows/row_records, not in a 'text' key. The scoring harness had a process-integrity bug where the score-stage never consumed persisted MinIO metas after ingestion succeeded, defaulting all 24 docs to ERROR with null node_count/chars. The RFC-025 D4 'pre-publish verification' protocol (mandatory live re-pull from MinIO before publishing any audit figures) became a critical safeguard but is a process workaround, not a root-cause fix.

The generative mechanism operates through measurement tools making the same structural assumptions as the code they audit, creating a feedback loop where both the defect and the measurement are blind to the same data:
- a. RFC-022's B1-Fix and the corpus audit's own char-count diagnostic both sum block.get('text',''), which returns 0 for every table block — so table-heavy documents appear to have catastrophically low content, and a fix aimed at promoting them inherits the identical blind spot (chain 7).
- b. The scoring harness never consumed persisted MinIO metas, defaulting all 24 documents to ERROR — completely masking the true corpus state (chain 16).
- c. RFC-025 D4 pre-publish verification became mandatory practice but does not fix the harness bug — it prevents publication of false results based on that bug (chain 17).

The meta-problem is that 'zero violations' or 'all ERROR' measurements are taken at face value when they reflect detector/harness failures, driving wrong remediation decisions.

## Code Evidence

`save_doc_meta` at storage/verdict.py:78-185 shows _MERGE_FIELDS defining persisted sidecar fields. `TreeSignals.from_tree` computes flat_text by flattening tree node text, which ignores table block structure (headers/rows/row_records carry content, not block['text']). `_try_image_enrichment` at verdict.py:243 computes total_chars = len(_dedupe_chart_text_lines(sig.primary_text)). The harness reads verdict from MinIO sidecar via read_registry_fields, but the scoring stage failed to invoke this path, defaulting to ERROR.

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/storage/verdict.py | Sidecar persistence & field merging |
| src/pageindex_mcp/helpers/verdict.py | Char-count computation & scoring |
| src/pageindex_mcp/helpers/garble.py | Detection metrics & signal computation |
| src/pageindex_mcp/worker/registry_mirror.py | Registry field hydration |
