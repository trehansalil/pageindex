---
zone_name: Converter Pipeline and Deployment Gap
severity: high
bug_count: 3
status: improved
audit_date: 2026-08-12
audit_run: POST
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
key_files:
  - src/pageindex_mcp/converters/pipeline.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - high
  - converter-chain
  - agpl-compliance
scorecard_verdict: regressed
scorecard_date: 2026-08-12
scorecard_run: POST
---
## Mechanism

ConverterFailurePolicy (pipeline.py:64-94) classifies failures as transient or structural and checks whether the next converter is AGPL-licensed. BLOCK_AGPL only fires for transient failures when the next converter is AGPL; structural failures always take the WALK branch.

This means a parse error or import failure (structural) in the primary non-AGPL converter silently routes through pymupdf4llm (AGPL-3.0) with only a logger.warning.

The remote Docling microservice is a separate deployment with no version-check or contract enforcement — indexer.py never forwards expected_script to the remote converter, so every document routed through it still gets headings unconditionally reversed.

The bidi heading guard (_heading_is_logical_order) exists only in the local working tree with zero commits in git history.

## Code Evidence

**ConverterFailurePolicy** (pipeline.py:64-94): WALK='walk' applies to structural failures; BLOCK_AGPL only fires for transient+AGPL combination.

**Config flags**: PDF_CONVERTER (default 'docling') and ALLOW_AGPL_FALLBACK (default '1') control AGPL exposure together.

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/converters/pipeline.py | Failure policy & chain walking |
| src/pageindex_mcp/client/indexer.py | Remote routing & parameter forwarding |
| src/pageindex_mcp/config.py | Converter selection & fallback flags |

## Related Zones

- [[garble-detection-kernel]] (AGPL converters produce different garbling)
