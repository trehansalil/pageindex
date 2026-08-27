---
zone_name: OCR Pipeline Filter Composition and Re-entry Hazards
severity: critical
bug_count: 15
status: audited
audit_date: 2026-08-26
audit_run: POST-FIX-13
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-13.md
key_files:
  - src/pageindex_mcp/picture_plane.py
  - src/pageindex_mcp/converters/pictures.py
  - src/pageindex_mcp/client/recovery.py
  - src/pageindex_mcp/client/indexer.py
tags:
  - zone-spec
  - critical
  - ocr
  - pipeline
---
## Mechanism

The generative mechanism is **ORDER-DEPENDENT FLAG INTERACTION** across multiple decision sites. `decide_ocr_strategy` (picture_plane.py:357-430) sequences five branches: re-entry guard, UNIFIED_OCR_PLAN_ENABLED image-doc branch, force_full_page, per-picture escalation, and NONE fallback. Each branch short-circuits before later ones, so adding or modifying any branch changes which documents reach downstream branches. 

The UNIFIED_OCR_PLAN_ENABLED branch explicitly runs AFTER the re-entry guard (a Zone-2 fix for a concrete prior bug where image docs bypassed the guard), but callers in recovery.py set full_page_already_applied in one code path and read it in another (picture_plane.py), creating cross-module state coupling.

Meanwhile, `_text_layer_has_content` (pictures.py:267-299) gates per-picture OCR with a char-count floor AND a garble check, but the page-level text-layer check upstream can short-circuit before clip_text extraction runs — a concrete bug that recurred from RFC-018 through RFC-025.

The marker-count-duplication workaround (client.py creates N duplicate PictureResults to satisfy splice_figure_markers's count guard) adds another interacting constraint: adjusting picture classification or OCR routing changes the marker count, which changes whether the splice guard passes, which changes enrichment results.

## Code Evidence

- `decide_ocr_strategy` (picture_plane.py:357-430): re-entry guard at line 389 returns `OcrDecision(mode=NONE)` when `full_page_already_applied=True`. UNIFIED_OCR_PLAN_ENABLED branch at line 403 runs AFTER the guard — but the comment at line 399-402 explicitly documents this as a Zone-2 fix for a prior ordering bug where image docs bypassed the guard. UNIFIED_OCR_PLAN_ENABLED defined at picture_plane.py:350 as os.getenv default 'false'.

- `_text_layer_has_content` (pictures.py:267-299): two-stage gate — char-count floor (len <= _PICTURE_OCR_MIN_CHARS → False), then unconditional detect_garble call ('always on', D0 RFC-023).

- `decide_ocr_mode` (picture_plane.py:438-456): still exists as a legacy wrapper delegating to decide_ocr_strategy, confirming dual-site decision pattern was only partially consolidated.

- `RecoveryMixin._execute_ocr_retry` (recovery.py:83-316): sets full_page_already_applied at line 178 and reads it at line 107.

## Related RFCs

RFC-018→019→020→021/022→024/025: Chain 1 spanning picture-OCR filter composition and multiple regressions.

RFC-018 D3b: node_garbling never recognized by OCR-escalation (Chain 12).

RFC-018 D0: marker-count-duplication workaround introduced (Chain 14).

RFC-025 D1: clip_text execution gap finally found (Chain 15, Human Rights doc 503k→382 chars).
