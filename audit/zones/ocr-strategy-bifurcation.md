---
zone_name: OCR Strategy Bifurcation
severity: critical
wave: 1
priority: 3
status: triaged
audit_date: 2026-08-25
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-12_POST.md
tags:
  - zone-spec
  - critical
  - wave-1
---
## Mechanism to Eliminate

Three independent OCR entry points (page-level escalation in the PDF branch via decide_ocr_strategy, per-picture crop OCR in _recover_picture_text, standalone-image OCR hardcoded in the elif ext in _IMAGE_EXTS block) make structurally coupled decisions independently. The standalone-image branch bypasses decide_ocr_strategy entirely, hardcodes lang lists instead of calling detect_ocr_langs, skips splice_picture_text_for_tree before tree construction, and duplicates constants across two files. Each filter added for one document class silently degrades another because there is no single decision point that accounts for document_type.

## Strategy

Consolidate + type-safe contract. Extend decide_ocr_strategy with a document_type discriminant and ocr_langs output so ALL file types (PDF, image, HTML, XLSX, text) route through one decision point producing an OcrPlan. Replace the standalone-image inline block with a call to the unified decision point followed by a shared _execute_ocr_plan helper. Deduplicate constants into images.py as the canonical location. Narrow _tesseract_ocr_image exception handling and add a Prometheus counter. Add splice_picture_text_for_tree call to the standalone-image path. 

**Sequence:**
1. Hardcoded lang fix (1 line)
2. Constant dedup
3. Exception narrowing + metric
4. Splice insertion for standalone images
5. OcrPlan unification behind UNIFIED_OCR_PLAN_ENABLED feature flag

## Code Targets

| File | Lines | What | How | Constraint |
|---|---|---|---|---|
| src/pageindex_mcp/client/indexer.py | 658 | Replace hardcoded OCR langs with detect_ocr_langs | Change line 658 from `img_langs = await asyncio.to_thread(ensure_tessdata, ["ara", "deu", "eng"])` to `img_langs = await asyncio.to_thread(ensure_tessdata, detect_ocr_langs(filename))`. detect_ocr_langs is already imported at line 33. | detect_ocr_langs returns ['deu','eng'] for empty input, preserving prior default for non-Arabic files |
| src/pageindex_mcp/client/indexer.py | 209,220-222,227 | Remove duplicated constants; import from images.py | Delete the local definitions of _IMAGE_EXTS (line 209), MIN_STANDALONE_IMAGE_MD_CHARS (line 227), and _IMAGE_STANDALONE_PIPELINE_ENABLED (lines 220-222). Add `from .images import _IMAGE_EXTS, MIN_STANDALONE_IMAGE_MD_CHARS, _IMAGE_STANDALONE_PIPELINE_ENABLED` to imports. Keep _SUPPORTED computed from imported _IMAGE_EXTS. | _SUPPORTED at line 210 must still be computed as the union of the explicit set and _IMAGE_EXTS |
| src/pageindex_mcp/client/indexer.py | 679-685 | Insert splice_picture_text_for_tree before md_to_tree | After line 679 (state.md_content = md_content), before writing temp file, add: `if state.pic_results and TREE_PATH_PICTURE_SPLICE_ENABLED: md_content = splice_picture_text_for_tree(md_content, state.pic_results); state.md_content = md_content`. Mirrors PDF path at lines 587-589. splice_picture_text_for_tree imported at line 42. | Must be gated on TREE_PATH_PICTURE_SPLICE_ENABLED to match PDF path behavior |
| src/pageindex_mcp/converters/pictures.py | 249-259 | Narrow exception handling in _tesseract_ocr_image | Replace `except Exception as exc` (line 257) with `except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError, OSError) as exc`. Add `from ..metrics import TESSERACT_OCR_FAILURE_TOTAL` import and increment `TESSERACT_OCR_FAILURE_TOTAL.labels(reason=type(exc).__name__).inc()` before logger.warning call. | Must still return '' on failure (never raise) per HR3 contract -- local OCR failures must not propagate |
| src/pageindex_mcp/metrics/definitions.py | new | Add TESSERACT_OCR_FAILURE_TOTAL counter | Add `TESSERACT_OCR_FAILURE_TOTAL = Counter('pageindex_tesseract_ocr_failure_total', 'Tesseract per-picture OCR failures', ['reason'])` following existing Counter naming (e.g. AGPL_FALLBACK_TOTAL at line 193). Export from metrics/__init__.py. | Label 'reason' carries the exception class name for failure triage |
| src/pageindex_mcp/picture_plane.py | 26-46,344-386 | Extend OcrDecision + decide_ocr_strategy | Add `document_type: Literal['pdf','image','html','text','xlsx'] = 'pdf'` parameter to decide_ocr_strategy. Add `ocr_langs: list[str]` and `splice_required: bool` fields to OcrDecision. When document_type='image', set mode=FULL_PAGE, splice_required=True. For PDF, preserve existing logic. Gate behind UNIFIED_OCR_PLAN_ENABLED env var (default false). | Must be backward-compatible: existing callers with no document_type get identical behavior (pdf default). OcrDecision must remain frozen (dataclass(frozen=True)). |
| src/pageindex_mcp/client/recovery.py | 416 | Fix _recover_image_dominant_ocr keep-best logic | Change `use_keep_best=False` to `use_keep_best=True` in _execute_ocr_retry call at line 416. Ensures keep-best heuristic (lines 210-297) compares pre-retry vs post-retry quality before replacing, preventing RFC-027 D2 / RFC-028 D4 regression. | Existing garble/low_content recovery paths already pass use_keep_best=True (lines 342, 376), making this a consistency fix |

## Wiring Checks

| Symbol | Must Be Imported By | Check Type |
|---|---|---|
| TESSERACT_OCR_FAILURE_TOTAL | src/pageindex_mcp/converters/pictures.py, src/pageindex_mcp/metrics/__init__.py | import |
| _IMAGE_EXTS | src/pageindex_mcp/client/indexer.py | import |
| MIN_STANDALONE_IMAGE_MD_CHARS | src/pageindex_mcp/client/indexer.py | import |
| _IMAGE_STANDALONE_PIPELINE_ENABLED | src/pageindex_mcp/client/indexer.py | import |
| detect_ocr_langs | src/pageindex_mcp/client/indexer.py | call |
| splice_picture_text_for_tree | src/pageindex_mcp/client/indexer.py | call |
| decide_ocr_strategy | src/pageindex_mcp/client/indexer.py | call |

## Test Requirements

| Test File | What to Test | Assertion Type |
|---|---|---|
| tests/test_ocr_decision.py | decide_ocr_strategy with document_type='image' returns OcrDecision with mode=FULL_PAGE, splice_required=True; document_type='pdf' preserves existing truth table; OcrDecision.ocr_langs defaults to ['deu','eng'] | exhaustiveness |
| tests/test_image_blocks.py | Standalone image path calls splice_picture_text_for_tree before md_to_tree: given .jpg input with OCR text in pic_results, tree must contain OCR-recovered text. Also test TREE_PATH_PICTURE_SPLICE_ENABLED=false skips splice. | regression |
| tests/test_image_blocks.py | Standalone image path calls detect_ocr_langs(filename) instead of hardcoded list: given filename with Arabic characters, ensure langs to ensure_tessdata include 'ara' | regression |
| tests/test_rfc_converters.py | _tesseract_ocr_image increments TESSERACT_OCR_FAILURE_TOTAL on subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError, OSError; returns '' in all cases; does NOT catch KeyboardInterrupt | contract |
| tests/test_rfc_promotions.py | _recover_image_dominant_ocr uses keep-best heuristic: when OCR retry produces fewer chars than pre-retry, pre-retry content is preserved (state.md_content reverts) | regression |
| tests/test_client.py | MIN_STANDALONE_IMAGE_MD_CHARS and _IMAGE_EXTS are imported from images.py in indexer.py (no local redefinition); verify via monkeypatch that changing images.MIN_STANDALONE_IMAGE_MD_CHARS affects indexer behavior | wiring |

## Corpus Validation

- **Affected documents:** Standalone .jpg/.png/.jpeg/.tiff/.tif images in doc_store/, Arabic scanned PDFs (detect_ocr_langs regression class), image-dominant PDFs (keep-best guard)
- **Expected verdict direction:** improve
- **Spot check count:** 5

## Dependencies

None

## Complexity

medium
