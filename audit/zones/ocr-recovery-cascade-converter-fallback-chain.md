---
zone_name: OCR Recovery Cascade & Converter Fallback Chain
severity: high
bug_count: 8
status: improved
audit_date: 2026-09-01
audit_run: POST-RFC041
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-09-01_POST-RFC041.md
key_files:
  - src/pageindex_mcp/helpers/gates.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/client/recovery.py
  - src/pageindex_mcp/converters/pipeline.py
  - src/pageindex_mcp/converters/pictures.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - critical
  - ocr-recovery
  - converter-chain
scorecard_verdict: regressed
scorecard_date: 2026-09-01
scorecard_run: POST-RFC041
validation_date: 2026-09-01
validation_notes: "2/3 structural claims refuted. AGPL fallback (Chain 9) FIXED via
  ConverterFailurePolicy.BLOCK_AGPL. Recovery ordering (Chain 23) REFUTED:
  GARBLING fires FIRST. Kill-switch (Chain 14) PARTIAL: split into 3 flags but
  indirect coupling remains. Real gap: zero-content early return bypass."
---
## Mechanism

The OCR recovery subsystem and converter fallback chain form the densest defect-generating zone in the codebase. Three structural coupling patterns make fixes here systematically break other behaviors:

1. **Kill-switch coupling (PARTIALLY CONFIRMED):** Original single `_OCR_ESCALATION` was split into three independent flags: `ocr_escalation_garble`, `ocr_escalation_per_picture`, `image_dominant_ocr_escalation_enabled`. However, the recovery eligibility predicates (`_eligible_garble`, `_eligible_low_content`, `_eligible_image_dominant` at gates.py:293-325) still share `image_dominant_ocr_escalation_enabled` as a gate, creating indirect coupling (Chain 14).

2. **Recovery ordering (REFUTED):** GARBLING is severity=0 in GATE_TABLE and fires FIRST, not after NODE_COUNT_LOW (severity=1). validate_tree evaluates ALL gates exhaustively. The real gap is the **zero-content early return** in evaluate_gates (verdict.py:175-183): `sig.node_count == 0` returns `hard_fail_verdict="FAIL"/"zero_content"` BEFORE any gate-specific recovery can run (Chain 23).

3. **Converter chain walk-through (FIXED):** The bare `continue` defeating BLOCK_AGPL (Chain 9) was fixed. Current code at indexer.py:660-730 implements a 5-way `ConverterFailurePolicy` with explicit `BLOCK_AGPL` and `GATE_AGPL_STRUCTURAL` branches that `break` instead of walking into AGPL converters.

Remaining structural risk: zero-content early return bypass and eligibility predicate coupling.

## History

- **Chain 1:** RFC-018 D0 marker-count mismatch generated N duplicate PictureResults sharing identical png_bytes.
- **Chain 2:** RFC-018 D1 clip-text probe left downstream gap where `_recover_picture_results` failed to set `skipped_reason`.
- **Chain 5:** RFC-040 D5 `ensure_tessdata` converted silent substitution into terminal job error at indexer.py:885 (MOU MOHRE PASS→ERROR).
- **Chain 9:** ISS-35 RETRY branch bare `continue` defeats `BLOCK_AGPL` with `CONVERTER_TRANSIENT_RETRY_COUNT=1`.
- **Chain 14:** `_OCR_ESCALATION` kill-switch gates both page-level retry AND per-picture crop-OCR.
- **Chain 15:** GateSpec recovery_fns dedup used tuple identity causing duplicate full-page OCR passes (now fixed — dedup by method name at indexer.py:1495-1504).
- **Chain 22:** RFC-025 D2 detection fires but no OCR escalation triggered.
- **Chain 23:** Image-only PDFs hit `node_count<3` BEFORE garbling evaluation, preventing OCR escalation.

## Code Evidence

1. **GATES list** at gates.py:359-446 shows `NODE_COUNT_LOW` (severity=1, recovery_fns=_recover_low_content_ocr+_recover_image_dominant_ocr) and `DEPTH_LOW` (severity=2, recovery_fns=_recover_image_dominant_ocr) both carry `_recover_image_dominant_ocr`; current method-name dedup at indexer.py:1495-1504 (`_fn_name in _fired_methods`) fixes old tuple-identity bug.

2. **Image-dominant OCR recovery** at recovery.py:470-512 is gated by `pipeline_config.image_dominant_ocr_escalation_enabled` and checks image-line ratio >50%, reachable only when `GateSpec.recovery_eligible` returns True for `NODE_COUNT_LOW` or `DEPTH_LOW`, not `GARBLING`.

3. **Converter chain** at pipeline.py:699-787 builds chain with `is_agpl=True` on pymupdf4llm entries.

## Key Files

| File | Role |
|------|------|
| gates.py:359-446 | GATES list with NODE_COUNT_LOW/DEPTH_LOW recovery ordering |
| recovery.py:470-512 | Image-dominant OCR recovery gating and escalation logic |
| indexer.py:1495-1504 | Method-name dedup for recovery_fns (fixing old tuple-identity bug) |
| pipeline.py:699-787 | Converter chain building with AGPL fallback |
| pictures.py | Picture extraction and OCR handling |
| config.py | Configuration of escalation toggles |
