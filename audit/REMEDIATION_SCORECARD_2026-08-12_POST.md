---
title: Remediation Scorecard — POST (2026-08-12)
date: 2026-08-12
type: audit/scorecard
tags:
  - audit
  - scorecard
  - remediation
  - post-fix
aliases:
  - POST scorecard
  - 2026-08-12 scorecard
pre_fix_audit: "[[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11]]"
post_fix_audit: "[[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST]]"
delta_report: "[[ZONE_DELTA_2026-08-12_POST]]"
verdict: REGRESSED
net_bug_delta: +4
bug_count_prior: 48
bug_count_current: 52
zones_closed: 3
zones_regressed: 4
zones_improved: 1
zones_new: 2
---

# Remediation Scorecard — POST (2026-08-12)

**Pre-fix audit:** [[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-24_POST-FIX-11]]
**Post-fix audit:** [[ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST]]
**Delta report:** [[ZONE_DELTA_2026-08-12_POST]]

## Verdict: REGRESSED

4 of 5 matched zones regressed with 3 escalating to critical severity; 2 new zones surfaced; net +4 bugs (48 to 52). Three zones closed (25 bugs eliminated) but gains offset by 14 new-zone bugs and regression growth across Garble Detection Fragmentation, OCR Strategy Bifurcation, and Verdict Promotion stacks. Only validate_tree Reason-String Dispatch improved (-3 bugs).

## Zones Closed (3)

| Zone Name | Was Severity | Bugs Eliminated |
|-----------|--------------|-----------------|
| Tree-vs-Flat Gate Asymmetry | critical | 11 |
| Pre-Tree Text Transforms vs Table/Block Integrity | critical | 9 |
| HR3 PII Egress Gap (Docling + VLM Silent Degradation) | medium | 5 |

## Zones Remaining (5)

| Zone Name | Severity | Bug Count | Status |
|-----------|----------|-----------|--------|
| Garble Detection Fragmentation | critical | 14 | regressed |
| OCR Strategy Bifurcation | critical | 12 | regressed |
| Verdict Promotion / Quality Gate Stack | critical | 11 | regressed |
| God-Function Orchestration with Duplicated Divergent Logic | high | 8 | regressed |
| validate_tree Reason-String Dispatch | high | 7 | improved |

## New Zones (2)

| Zone Name | Severity | Introduced By |
|-----------|----------|---------------|
| Multi-Store Dual-Write Consistency | high | Consolidation of prior dual-CAS and persistence-timing findings into distinct zone; findings span RFC-002 through Run 19 write-barrier regressions |
| Config Layering Split and Dead-Code Accumulation | medium | Audit scope expansion surfaced config-path bugs (`GarbleConfig.from_config` hardcodes `garble_digit_floor=500` at `src/pageindex_mcp/helpers/garble.py:463`, `ALLOW_AGPL_FALLBACK` frozen at import time `src/pageindex_mcp/config.py:38`) and historical dead-code entries now partially resolved by RFC-038/039 |

## Metrics

- **Net bug delta:** +4 (48 → 52)
- **Wiring status:** all_wired
- **Unwired symbols:** none

## Recommended Next Steps

**VERDICT: REGRESSED.** 4 of 5 matched zones regressed (3 escalated to critical), 2 new zones surfaced, net +4 bugs (48 to 52). Only validate_tree Reason-String Dispatch improved (-3 bugs). 3 zones closed (25 bugs eliminated) but offset by 14 new-zone bugs and regression growth.

### Chronic Pattern

This is the 12th scorecard; only 1 of 12 achieved CYCLE_COMPLETE (the 2026-08-17 run that dropped 64 to 48). Bug trajectory since: 48 → 53 → 53 → 66 → 77 → 52 (current). The codebase is accumulating diagnosis faster than it ships fixes. Four detailed simplification proposals with time estimates exist but ZERO implementations have landed across all of them.

### Priority Actions

1. **STOP FURTHER AUDIT CYCLES** until code lands. Each cycle surfaces more findings than it resolves, inflating bug count without convergence. The next action must be implementation, not diagnosis.

2. **IMPLEMENT existing proposals for the 3 critical zones** — Garble Detection has stalled 6+ consecutive cycles since RFC-013 D7 with zero code landed despite proposals; ScriptContext threading (estimated 6–10 hrs) is the single highest-leverage fix.

3. **Address Verdict Promotion gate-ordering bugs** (QF2a unreachable, synthetic structure PASS) — these are narrow code fixes to `apply_promotions()` sequencing, not architectural changes.

4. **OCR Strategy Bifurcation** needs the RegionMetadata/splice_figure_markers proposal (estimated 3–4 days) to structurally resolve the tree-path-never-calls-splice gap.

5. **The 2 new zones** (Multi-Store Dual-Write Consistency, Config Layering) should be addressed as part of existing proposals rather than new RFC tracks — Multi-Store overlaps with RFC-037 verdict CAS work, Config Layering is cleanup.

6. **Wiring is healthy** (all RFC-037/038/039 symbols confirmed wired via CodeGraph + grep), so the regression is in detection/logic correctness, not in plumbing — fixes must target algorithm calibration and gate ordering, not more wiring.
