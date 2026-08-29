---
zone_name: OCR Recovery Cascade
severity: high
bug_count: 5
status: audited
audit_date: 2026-08-12
audit_run: POST
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
key_files:
  - src/pageindex_mcp/picture_plane.py
  - src/pageindex_mcp/converters/ocr_langs.py
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/converters/pictures.py
tags:
  - zone-spec
  - high
  - ocr-recovery
  - kill-switch
---
## Mechanism

Detection-to-remediation gap: GATE_TABLE evaluates all 10 gates exhaustively, but OCR-recovery eligibility predicates (_eligible_garble, _eligible_low_content, _eligible_image_dominant) are narrower than the full gate set. When GATE_TABLE severity ordering lets node_count_low mask garbling, the document's reported reason may not match the recovery-eligible set, so recovery never wires up despite correct detection.

The single kill-switch problem (decide_ocr_strategy, picture_plane.py:357-430): the ocr_escalation_enabled parameter gates the PER_PICTURE branch, but disabling it also prevents page-level escalation.

The marker-removal gap: the 60%-page-area coverage filter correctly suppresses OCR, but splice_figure_markers neutral-marker fallback preserves literal `<!-- image -->` verbatim in output.

ensure_tessdata now raises TessdataUnavailableError for non-Latin scripts, but the all-Latin-languages-dropped path still falls back to ['deu','eng'] regardless of request.

## Code Evidence

**decide_ocr_strategy** (picture_plane.py:357-430): ordered if-chain with single ocr_escalation_enabled parameter gating both page-level and per-picture.

**GATES definition** (gates.py:359-446): GARBLING at severity=0, NODE_COUNT_LOW at severity=1 with different recovery eligibility.

**ensure_tessdata** (ocr_langs.py:92-196): raises TessdataUnavailableError but empty-available path returns hardcoded fallback.

## Key Files

| File | Role |
|------|------|
| src/pageindex_mcp/picture_plane.py | OCR strategy & kill-switch |
| src/pageindex_mcp/converters/ocr_langs.py | Language availability & fallback |
| src/pageindex_mcp/helpers/gates.py | Recovery eligibility predicates |
| src/pageindex_mcp/converters/pictures.py | Picture recovery wiring |

## Related Zones

- [[verdict-gate-cascade]] (severity ordering masks garble)
- [[garble-detection-kernel]] (detection decoupled from recovery)
