---
zone_name: OCR Recovery Cascade
severity: high
bug_count: 4
status: improved
audit_date: 2026-08-27
audit_run: POST-RUN20
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-27_POST-RUN20.md
key_files:
  - src/pageindex_mcp/client/recovery.py
  - src/pageindex_mcp/picture_plane.py
  - src/pageindex_mcp/converters/pictures.py
  - src/pageindex_mcp/client/indexer.py
tags:
  - zone-spec
  - high
  - ocr
  - recovery
scorecard_verdict: needs_another_cycle
scorecard_date: 2026-08-27
scorecard_run: POST-RUN20
---
## Mechanism

_execute_ocr_retry is the single densest sequential-cascade-where-order-matters gate in the codebase: 234 lines, cyclomatic complexity 20, with fixes from at least 5 zone remediation waves (Zone-1/2/6/7/8) layered into one function. It is the shared tail for three independent recovery paths (_recover_garble_ocr, _recover_low_content_ocr, _recover_image_dominant_ocr), each with per-method eligibility checks but shared execution. The keep-best heuristic has 4 sequential stages where each only runs if the prior did not decide. The OCR-mode decision surface retains a split: decide_ocr_strategy (picture_plane.py:357-430) is the 'unified' successor, but decide_ocr_mode (now a thin wrapper, picture_plane.py:438-458) is called by _recover_picture_results (pictures.py:1102) without passing document_type or ocr_langs, losing Zone-8 parameters. Detection-without-remediation gaps exist: garble detection fires at the verdict stage but no OCR recovery is wired to that output path.

The generative mechanism operates through accreted complexity in an ordering-dependent cascade:
- a. D1's coverage-filter (skip OCR when >60% page area) has no matching marker-removal step, so deliberately-skipped regions leave literal `<!-- image -->` markers in output (chain 1).
- b. The _OCR_ESCALATION kill-switch gates BOTH page-level OCR escalation and per-picture crop OCR, so toggling it for one behavior disables the other (chain 2).
- c. The keep-best cascade's strict ordering means adding a new stage at the wrong position overrides existing conclusions without detection by single-stage tests (chain 2).
- d. The garble gate correctly fires on verdict-stage garbling, but OCR escalation is wired only to early-stage validation failures — detection landed, recovery hook missing (chain 20).
- e. Large-file processing dies mid-flight with no artifacts persisted and no diagnostic data (chain 21).

## Code Evidence

`_execute_ocr_retry` at recovery.py:83-316 implements the full cascade. Keep-best stages at recovery.py:241-290: (a) Zone-8 zero-char shortcut: `if pre_retry.total_chars == 0 and post_retry_chars > 0: retry_wins = True`, (b) char-count: `elif post_retry_chars < pre_retry.total_chars: retry_wins = False`, (c) equal-count garble tiebreak calls detect_garble on pre/post (line 256-268), (d) RFC-029 D4 density: `_density_improved = _post_density < _pre_density * 0.80`. `decide_ocr_mode` at picture_plane.py:438-458 now delegates to decide_ocr_strategy but `_recover_picture_results` (pictures.py:1102) calls it without document_type/ocr_langs kwargs. trace_path confirms sole caller is _recover_picture_results.

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/client/recovery.py | OCR retry cascade & keep-best heuristic |
| src/pageindex_mcp/picture_plane.py | OCR strategy & mode decision |
| src/pageindex_mcp/converters/pictures.py | Picture-region OCR execution |
| src/pageindex_mcp/client/indexer.py | Recovery path invocation |
