---
tags:
  - zone-index
audit_date: 2026-08-26
audit_run: POST-FIX-12
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-12.md
---
# Architecture Defect Zones Index

**Audit Date:** 2026-08-26  
**Audit Run:** POST-FIX-12  
**Total Zones:** 8  
**Total Bugs Attributed:** 49  

## Zone Priority Table (Severity-Ordered)

### Critical Severity (3 zones)

| # | Zone | Bug Count | Key Finding | Link |
|---|------|-----------|-------------|------|
| 1 | Garble Detection Surface Fragmentation | 10 | Five independently-sufficient blind spots where NFKC normalization destroys signals before garble detectors can inspect them; self-referential script detection on corrupted text; Latin-gibberish scope narrowing; silent tessdata fallback to deu/eng | [[garble-detection-surface-fragmentation]] |
| 2 | Verdict Gate Promotion Bypass Cascade | 8 | image_enrichment_promoted at priority=100 explicitly outranks max_leaf_ratio hard-fail; four zero-char Arabic docs FAIL/ERROR→MARGINAL from hysteresis defeat via corpus reingestion; violates Hard Rule 5 | [[verdict-gate-promotion-bypass-cascade]] |
| 3 | OCR Pipeline Flag Conflation and Re-entry Hazards | 7 | UNIFIED_OCR_PLAN_ENABLED branch checked before full_page_already_applied re-entry guard; keep-best guardrail makes retry arithmetically impossible for no-text-layer PDFs (69% loss reverted every time) | [[ocr-pipeline-flag-conflation-and-re-entry-hazards]] |

### High Severity (3 zones)

| # | Zone | Bug Count | Key Finding | Link |
|---|------|-----------|-------------|------|
| 4 | Content-Destructive Heuristic Chains | 6 | RFC-034 D11 ToC stripping collapsed Penal Code 493/595 nodes; RFC-034 D16 guard over-corrects into 88% body-less fragments; RFC-029 D3 fence-marker parity toggle silences all subsequent content after stray backtick | [[content-destructive-heuristic-chains]] |
| 5 | Verdict Persistence Competing Writers | 5 | MinIO sidecar written by converters_cli child, then overwritten by worker parent; backfill best-effort non-fatal; Python/SQL CAS logic asymmetric; flat-doc triple-write path bypasses consolidation; violates Hard Rule 2 | [[verdict-persistence-competing-writers]] |
| 6 | Landscape/Rotation and Remote Route Divergence | 5 | Rotation correction docling-only (not pymupdf4llm fallback); two landscape detectors with contradictory predicates; metadata/data mismatch (original PDF vs. rotation-normalized temp); stale remote Scaleway image with RFC-033 D2 heading guard never deployed | [[landscape-rotation-and-remote-route-divergence]] |

### Medium Severity (2 zones)

| # | Zone | Bug Count | Key Finding | Link |
|---|------|-----------|-------------|------|
| 7 | Image Block Conflation and Marker Survival | 4 | Per-picture OCR text relocated from prose to image blocks (invisible to content_class); _recover_picture_results fallback cannot distinguish 'tried, found nothing' from 'never tried'; image_to_markdown() path never wired to enrichment pipeline | [[image-block-conflation-and-marker-survival]] |
| 8 | Verified-Locally-Never-Deployed Fix Drift | 4 | RFC-033 D2 heading guard exists in no commit despite git log search; RFC-027 chunked_docling_timeout_s created but never wired; _check_bidi_coherence 0%-TPR promoted to default-true (null-detector fallacy) | [[verified-locally-never-deployed-fix-drift]] |

## Cross-Cutting Themes

1. **Interface-level fixes without ordering/arithmetic fixes** — consolidation without addressing underlying mechanisms
2. **Verified-locally-but-never-deployed fixes** — documentation/deployment gap (task files vs. commits vs. remote service)
3. **Two independent verdict engines that can disagree** — validate_tree gate-table vs. classify_verdict grouped-rule engine
4. **Duplicated logic drifting apart** — fix-one-miss-the-other pattern across 3+ RFC generations
5. **Threshold ratcheting as symptom management** — PASS_MAX_LEAF_RATIO widened 0.17→0.20→0.30, each with documented-but-realized risks
6. **Fix-for-a-fix chains producing opposite failure modes** — stripping heuristics causing content loss, guards over-correcting into different failure modes
7. **Shared kill-switches gating independent mechanisms** — _OCR_ESCALATION controlling both page-level and per-picture OCR
8. **Detection instruments misread as clean bills of health** — 0%-TPR detectors treated as safety evidence (null-detector fallacy)
9. **Script/language-detection blind spots recurring across Arabic/RTL pipeline** — 5+ RFCs (010, 015, 018, 026, 027, 028, 033) without closing the class
10. **Cross-process/cross-store consistency races** — verdict persisted by converters_cli child, overwritten by worker parent with no transactional boundary
11. **Right-to-erasure cascade gaps** — Hard Rule 2 violations in registry upsert and verdict persistence
12. **Detection without remediation** — garble gate fires but recovery not wired; promotion bypasses gates without notice

## Severity Distribution

| Severity | Count | Bug Count |
|----------|-------|-----------|
| critical | 3 | 25 |
| high | 3 | 16 |
| medium | 2 | 8 |
| **Total** | **8** | **49** |

## File Impact Summary

Most frequently implicated files across all zones:

| File | Zones | Frequency |
|------|-------|-----------|
| src/pageindex_mcp/converters/normalize.py | 1, 4, 6, 8 | 4 |
| src/pageindex_mcp/picture_plane.py | 3, 6, 7 | 3 |
| src/pageindex_mcp/helpers/verdict.py | 2 | 1 |
| src/pageindex_mcp/helpers/garble.py | 1 | 1 |
| src/pageindex_mcp/config.py | 2, 3, 6 | 3 |

## Next Steps

1. Review zones in severity order (critical, then high, then medium)
2. Investigate cross-zone dependencies (e.g., Zone 1's garble gate feeds Zone 2's verdict promotion)
3. Prioritize architecture refactors that address multiple cross-cutting themes
4. Establish automated parity checks for local/remote routes (Zone 6)
5. Consolidate duplicate logic (Zone 8: fix-one-miss-the-other drift)
6. Add transactional boundaries for multi-store verdict persistence (Zone 5)

---

**Report:** [[../ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-26_POST-FIX-12]]
