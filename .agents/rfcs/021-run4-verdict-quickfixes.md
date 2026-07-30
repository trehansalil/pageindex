<!-- Space: CITRA -->
<!-- Title: RFC-021: Run 4 Verdict Quick-Fixes — Threshold Tuning, Garble Gate Precision, OCR Deferral -->
<!-- Folder: RFCs -->

# RFC-021: Run 4 Verdict Quick-Fixes — Threshold Tuning, Garble Gate Precision, OCR Deferral

## Status

- Status: DRAFT
- Author: Salil Trehan + Claude
- Date: 2026-07-28
- Branch: `feat/run4-verdict-quickfixes`
- Supersedes: Builds on RFC-020

## Problem Statement

Run 4 corpus reaudit (25 docs, all RFC-020 F0-F5 fixes applied) scored **13 PASS / 9 MARGINAL / 2 FAIL / 1 ERROR** -- a clear improvement over Run 3's 8/11/5/1 but still short of the target. Analysis of the 9 MARGINAL verdicts shows that **6-7 are caused by fixable code bugs or overly harsh thresholds**, not genuine extraction limitations. They fall into four categories:

| Category | Docs affected | Root cause |
|---|---|---|
| QF1: Forced-OCR regression | 7 (MOU MOHRE), 20 (Labor Exec Regs), 21 (Domestic Workers) | Pre-garble probe forces OCR upfront, destroying PictureItems and collapsing tree |
| QF2: Verdict threshold harshness | 8 (Reitlehrer), 13 (Pie chart), 14 (UAE landscape), 19 (Data Governance) | PASS gate too strict for small/flat docs with good content |
| QF3: Garble false positive on bilingual | 17 (SLA Agreement) | Garble gate misreads markdown formatting in Arabic/English bilingual as corruption |
| QF4: verdict_reason from input, not output | 20 (Labor Exec Regs), 21 (Domestic Workers) | Stored verdict reflects input text-layer probe, not final output quality |

Fixing these four categories should promote 6-7 MARGINAL to PASS, projecting Run 5 at **19-20 PASS / 2-3 MARGINAL / 2 FAIL / 1 ERROR**.

## Root Cause Analysis

### QF1: F2+D2 Forced-OCR Regression (Docs 7, 20, 21)

**Failure chain.** Three Arabic-filename PDFs with corrupt text layers are MARGINALed because the pre-garble probe forces full-page OCR on the *primary* conversion attempt, which destroys Docling's PictureItem segmentation and collapses the tree to flat.

The chain executes as follows:

1. **`helpers.py:725-735` -- `_script_from_filename()`** derives `expected_script="Arab"` from Arabic filenames. This was added by RFC-020 F2 to enable garble detection on docs whose text layer is too corrupt for `_infer_script()` to work.

2. **`client.py:543-550` -- pre-garble probe** opens the PDF with fitz, reads page 0's raw text, and calls `_flat_text_is_garbled(raw_text, expected_script=expected_script)`. Because the text layer IS corrupt and the expected_script is now correctly "Arab", the garble gate fires: `pre_garbled = True`.

3. **`client.py:553-556` -- OCR forced on primary attempt.** When `pre_garbled=True` and converter is docling, the code calls `conv_fn(file_path, True)` -- force_full_page_ocr=True on the FIRST conversion. Docling under force_full_page_ocr reclassifies PictureItems as TextItems.

4. **0 PictureResults returned.** With no PictureItems, `_recover_picture_text` produces an empty list. RFC-020 F0's `splice_picture_text_for_tree` receives no data to splice.

5. **Tree collapses to flat.** The markdown fed to `md_to_tree` has only bare `<!-- image -->` markers with no recovered text. `validate_tree` fails (node_count or depth insufficient), document flat-routes, and the flat path's `classify_verdict` cannot promote it above MARGINAL.

**The design error:** The pre-garble probe was intended to SKIP a wasted non-OCR conversion, but it does so at the cost of destroying the Docling pipeline's ability to segment pictures. The existing Fix-3 retry path (`client.py:729-751`) already handles OCR escalation correctly -- it runs AFTER the primary attempt fails validation, preserves the language override via `detect_ocr_langs`, and unions filename + content signals.

**Blast radius:** Every Arabic-filename PDF with a corrupt text layer. Currently docs 7, 20, 21; any future Arabic scanned additions will hit the same path.

### QF2: Verdict Threshold Harshness for Small/Flat Docs (Docs 8, 13, 14, 19)

**Code path.** `classify_verdict()` at `helpers.py:863-915` applies the PASS gate:

```python
# helpers.py:883-884
if node_count >= 3 and depth >= 2 and max_leaf_ratio < 0.15 and not garbled:
    return "PASS", ""
```

Four documents fail this gate despite having correct, complete content:

**Doc 8 (Reitlehrer):** Single-page German doc, 10 nodes, clean content. Fails because `depth=1` (flat structure with no nesting) or `max_leaf_ratio` barely exceeds 0.15. The content is fully extracted and readable -- the structural metrics penalize a legitimately flat document.

**Doc 13 (Pie chart):** Standalone image classified `flat_prose`. Only 2 blocks after extraction. Cannot meet `node_count >= 3`. Both images were enriched perfectly via RFC-020 F4. The cat_b promotion path (`helpers.py:895-897`) also requires `node_count >= 3`, so there is no escape hatch for image-dominant documents.

**Doc 14 (UAE landscape):** Chart document, also `flat_prose`, 4/5 images enriched. Same `node_count` / structural-depth issue as doc 13 -- the content is captured in enriched image blocks, not in tree nodes.

**Doc 19 (Data Governance):** Tree with depth 1, `max_leaf_ratio=0.16` -- ONE hundredth over the 0.15 PASS threshold. All 7 sections present. The cat_c promotion path (`helpers.py:898-904`) uses `CATEGORY_BC_PROMOTION_THRESHOLD` (0.17) but applies it only to non-flat, non-OCR docs with `hash_pipe_ratio < 0.01`. Doc 19 likely falls through because it is not garbled but depth=1 prevents the primary PASS gate from firing, and the promotion paths have their own restrictions.

**The design error:** The PASS gate treats structural depth/node metrics as proxies for content quality. For small documents (1-2 pages), flat/image-dominant documents, and near-threshold cases, the structural metrics diverge from actual extraction quality.

### QF3: Garble Gate False Positive on Bilingual Docs (Doc 17)

> **AUDIT CORRECTION (2026-07-28):** The original QF3 root cause analysis was written without verifying the code. Two proposed sub-fixes (QF3a markdown-token exclusion, QF3b bilingual guard) are **provable NO-OPs** against the actual regex patterns. This section is corrected to reflect the true code behavior and replaces the sub-fixes with a diagnosis-first approach.

**Code path.** Doc 17 (SLA Agreement) is an Arabic/English bilingual document. The garble check at `classify_verdict()` (`helpers.py:881`) calls `_tree_is_garbled(structure)` with **no `expected_script` parameter** — it passes `None`.

**Why the originally proposed QF3a is a NO-OP:** `_LATIN_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")` (`helpers.py:568`). Markdown tokens like `---`, ` ``` `, `#`, `##` contain zero `[A-Za-z]` runs of length ≥2, so they **can never appear in `latin_tokens`**. The proposed `_MD_FORMAT_RE` exclusion filter would match nothing. Moreover, the Latin-gibberish prong is gated on `expected_script != "Latn"` (`helpers.py:651-655`), and `classify_verdict` passes `expected_script=None` — so this prong is **inactive** for the `classify_verdict` call path that determines doc 17's verdict.

**Why the originally proposed QF3b is a NO-OP:** `_MIXED_SCRIPT_RE` (`helpers.py:678-681`) matches only glued fragments: 1-8 chars of `[\x21-\x7E]` (printable ASCII excluding space). A match's Latin segment is ≤8 chars with no whitespace, so `latin_segment.split()` yields at most 1 token — the "≥3 common English words" guard can **never fire**.

**Actual root cause: UNDIAGNOSED.** The most probable firing mechanism is `_has_sparse_mojibake` (`helpers.py:684-696`), which checks `len(matches) / max(len(text.split()), 1) > threshold`. In a bilingual Arabic/English document, legitimate English phrases adjacent to Arabic text may produce enough `_MIXED_SCRIPT_RE` matches to exceed the 2% threshold. But this is a hypothesis — the actual garble prong firing for doc 17 must be measured, not guessed.

**Corrected approach: diagnosis phase required.** Before implementing any fix, run doc 17's flattened tree text through each garble sub-check (`_is_garbled_blob` sub-prongs + `_has_sparse_mojibake`) independently to identify which mechanism actually fires and with what values. The fix design follows from the diagnosis.

### QF4: verdict_reason from Input Probe, Not Output Quality (Docs 20, 21)

**Code path.** Docs 20 and 21 have corrupt input text layers but clean output after OCR processing. The stored `verdict_reason` reports "garbling" despite 0 garbled blocks in their output.

The flow:

1. **Tree path:** `validate_tree()` at `helpers.py:772-795` is called on the tree structure. The tree built from OCR-recovered content may be sparse, so validation fails (node_count < 3 or depth < 2), and the document falls through to the flat path.

2. **Flat path:** `classify_verdict()` at `client.py:980-984` is called with `validate_reason=None` (the tree-path failure reason is NOT forwarded):

   ```python
   # client.py:980-984
   f_verdict, f_verdict_reason = classify_verdict(
       flat_structure,
       content_class,
       None,  # <-- tree-path validate_reason not forwarded
   )
   ```

3. **Inside `classify_verdict()` (`helpers.py:881`):** The function calls `_tree_is_garbled(structure)` on the flat structure. Even though the body content is clean, the flat text includes residual garble from the OCR cover page or header region. A small amount of garbled text in the flattened blob can trip the garble gate, especially via `_has_sparse_mojibake()` which is length-independent.

4. **MARGINAL returned with reason="garbling" at `helpers.py:907-908`** even though the document body is clean and all expected sections are present.

**The design error:** The garble check operates on the entire flattened text including all preamble/cover-page noise. A small garbled prefix (cover page OCR artifacts) can poison the verdict for an otherwise clean document. There is no garble-ratio threshold -- any garble in the flattened text flags the whole document.

## Proposed Fixes

### QF1-Fix: Defer OCR Escalation from Pre-Garble Probe to Fix-3 Retry Path

**Change.** The pre-garble probe (`client.py:543-556`) should FLAG the garble but NOT force OCR on the primary conversion attempt. Instead, let the primary attempt run normally (preserving Docling's PictureItem segmentation), then rely on the existing Fix-3 retry path (`client.py:729-751`) to handle OCR escalation with correct language override.

**`src/pageindex_mcp/client.py:543-556`** -- modify the pre-garble probe:

Before:
```python
pre_garbled = False
try:
    import fitz
    with fitz.open(file_path) as probe_pdf:
        if probe_pdf.page_count > 0:
            raw_text = probe_pdf[0].get_text()
            if raw_text.strip() and _flat_text_is_garbled(raw_text, expected_script=expected_script):
                pre_garbled = True
                logger.info(
                    "D3a: raw text layer garbled for %s, forcing full-page "
                    "OCR upfront",
                    filename,
                )
except Exception:
    pass

# ... later (line ~553-556):
if pre_garbled and converter == "docling":
    md_content, pic_results = await conv_fn(file_path, True)
```

After:
```python
pre_garbled = False
try:
    import fitz
    with fitz.open(file_path) as probe_pdf:
        if probe_pdf.page_count > 0:
            raw_text = probe_pdf[0].get_text()
            if raw_text.strip() and _flat_text_is_garbled(raw_text, expected_script=expected_script):
                pre_garbled = True
                logger.info(
                    "D3a: raw text layer garbled for %s — flagged for "
                    "Fix-3 retry (NOT forcing OCR on primary attempt)",
                    filename,
                )
except Exception:
    pass

# REMOVE the forced-OCR primary attempt block entirely.
# The pre_garbled flag is now informational only — it can be used
# for logging/diagnostics. OCR escalation is handled exclusively
# by the Fix-3 retry path (client.py:729-751) which:
#   (a) runs AFTER primary attempt + validate_tree
#   (b) preserves PictureItem segmentation on the primary attempt
#   (c) passes correct ocr_lang_override via detect_ocr_langs
```

**Decision rationale:** The pre-garble probe was an optimization (skip wasted non-OCR attempt). But OCR-forcing destroys PictureItem segmentation, which is catastrophic for documents whose content is primarily in images/scanned pages. The Fix-3 retry path already handles this correctly -- it only fires when `validate_tree` returns `reason="garbling"`, and it passes the correct language override. The cost of removing the optimization is one wasted primary attempt for garbled docs (~2-5 seconds), which is acceptable given it prevents tree collapse.

**Env var.** `PRE_GARBLE_FORCE_OCR_ENABLED` (default `false`; set `true` to restore pre-RFC-021 behavior as a rollback lever). The rollback path MUST restore the current call including `ocr_lang_override=detect_ocr_langs(filename)` — the actual code (`client.py:565-571`) passes this override, not just `conv_fn(file_path, True)`.

> **AUDIT NOTE (2026-07-28):** Existing test `tests/test_client_contract.py` D3a block (lines 619-720) asserts that `conv_mock` is called with `(file_path, True, ocr_lang_override=...)` when page-0 text is garbled. QF1 inverts this behavior — **a task to update these D3a assertions must be added to the implementation plan** (neither Task 1.1 nor 1.2 originally covered this).

### QF2-Fix: Verdict Threshold Tuning for Small/Flat Docs

Three sub-fixes:

#### QF2a: Image-enrichment promotion path

**Approach: parameter-based.** Image enrichment happens in `client.py:_enrich_image_blocks()` (line 405), which stamps `figure_path`, `ocr_text`, `description` onto `{"role": "image"}` blocks in the flat `blocks` list — NOT in `structure`. The `enriched` boolean and `_iter_all_nodes()` helper do not exist in the codebase. Therefore the ratio MUST be computed in `client.py` from the flat `blocks` list after `_enrich_image_blocks` runs (line 969), then passed to `classify_verdict` as a parameter.

**`src/pageindex_mcp/client.py:969-984`** -- compute enrichment ratio from blocks, pass to classify_verdict:

```python
await _enrich_image_blocks(blocks, pic_results, doc_id)

# QF2a (RFC-021): compute image enrichment ratio from flat blocks.
# A block is "enriched" if it has non-empty ocr_text, description,
# or figure_path — fields stamped by _enrich_image_blocks().
image_blocks = [b for b in blocks if b.get("role") == "image"]
if image_blocks:
    enriched_count = sum(
        1 for b in image_blocks
        if b.get("ocr_text") or b.get("description") or b.get("figure_path")
    )
    image_enrichment_ratio = enriched_count / len(image_blocks)
else:
    image_enrichment_ratio = None  # no images → don't trigger promotion

# ... later at classify_verdict call:
f_verdict, f_verdict_reason = classify_verdict(
    flat_structure,
    content_class,
    None,
    image_enrichment_ratio=image_enrichment_ratio,  # QF2a
)
```

**`src/pageindex_mcp/helpers.py:863`** -- add parameter to `classify_verdict`:

```python
def classify_verdict(
    structure: list,
    content_class: str,
    validate_reason: str | None,
    image_enrichment_ratio: float | None = None,  # QF2a
) -> tuple[str, str]:
    ...
    # After existing cat_b/cat_c promotions (helpers.py:904),
    # BEFORE MARGINAL fallthrough:
    if (
        image_enrichment_ratio is not None
        and image_enrichment_ratio >= 0.8
        and content_class in ("flat_prose", "flat_mixed")
    ):
        return "PASS", "image_enrichment_promoted"
```

**Pre-condition:** docs 13/14 must have `max_leaf_ratio <= 0.75` to survive the FAIL gate (helpers.py:876-877) before reaching the promotion. Verify during Phase 2 implementation.

#### QF2a-LT: Dedicated Image-File Pipeline (Long-Term Follow-Up)

QF2a above is the immediate fix — a promotion path bolted onto the existing verdict system. But standalone image files (`.jpg`, `.png`, `.tiff`, `.bmp`, `.gif`, `.webp`) are **fundamentally different inputs**, not "flat prose documents with low node count." The text-oriented metrics (`node_count`, `depth`, `max_leaf_ratio`) are meaningless for a photo. The long-term fix is a dedicated pipeline.

> **AUDIT NOTE (2026-07-28):** An existing image route already exists: `client.py:231/681` routes `_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}` through Tesseract-OCR + synthetic `PictureResult`s (RFC-018 D0). QF2a-LT's early detection MUST reconcile with this route and its tests (`test_image_blocks.py`, `test_imgblock_audit_findings.py`). Additionally, `.gif`/`.webp` are NOT in `_SUPPORTED` (`client.py:232`) — they are rejected before reaching line 520 unless `_SUPPORTED` is also extended. Add a reconciliation task to Phase 6.

**Phase 1 — Early detection in `client.py:index()`** (around line 520):

```python
# Reconcile with existing _IMAGE_EXTS route (client.py:681).
# The existing route handles Tesseract-OCR + PictureResult synthesis.
# QF2a-LT adds image-specific verdict AFTER the existing route runs.
IMAGE_EXTENSIONS = _IMAGE_EXTS  # reuse existing set, not a new one
# .gif/.webp require _SUPPORTED update first — defer to a separate task.

ext = Path(file_path).suffix.lower()
if ext in IMAGE_EXTENSIONS:
    content_class = "image_standalone"
    # Route to image-specific verdict — still uses existing OCR pipeline
```

**Phase 2 — Image-specific verdict in `helpers.py:classify_verdict()`:**

```python
# At the top of classify_verdict, before any tree/flat logic:
if content_class == "image_standalone":
    return _classify_image_verdict(image_enrichment_ratio)

def _classify_image_verdict(image_enrichment_ratio: float | None) -> tuple[str, str]:
    """Verdict for standalone image files.

    Judges on enrichment quality, not tree structure.
    PASS requires: ≥80% of image blocks enriched (ratio computed in client.py).
    MARGINAL: images detected but enrichment ratio < 0.8.
    FAIL: no images detected at all (ratio is None).
    """
    if image_enrichment_ratio is None:
        return "FAIL", "no_images_detected"
    if image_enrichment_ratio == 0.0:
        return "MARGINAL", "images_not_enriched"
    if image_enrichment_ratio >= 0.8:
        return "PASS", "image_enriched"
    return "MARGINAL", f"image_enrichment_ratio={image_enrichment_ratio:.2f}"
```

**Phase 3 — Distinct storage/meta fields:**

The `meta.json` sidecar for image_standalone docs would carry image-relevant fields instead of tree metrics:

```python
meta = {
    "doc_id": doc_id,
    "content_class": "image_standalone",
    "total_images": len(image_blocks),
    "enriched_images": enriched_count,
    "enrichment_methods": ["ocr", "vlm"],  # which methods produced data
    "verdict": verdict,
    "verdict_reason": verdict_reason,
    # no max_leaf_ratio, no node_count, no depth — meaningless for images
}
```

**Why both approaches:**
- **QF2a (immediate):** Ships in Phase 2 of this RFC. Unblocks Docs 13, 14 now. Minimal code change.
- **QF2a-LT (follow-up):** Ships as Phase 6 (new). Architecturally correct. Ensures future image-only uploads are never judged by text metrics. Any `image_standalone` doc automatically gets the right verdict path.

**Estimated effort for QF2a-LT:** 1 day (early detection + verdict function + meta changes + tests).

**Env var.** `IMAGE_STANDALONE_PIPELINE_ENABLED` (default `true`; set `false` to fall back to QF2a promotion path).

#### QF2b: Relax max_leaf_ratio for primary PASS gate

**`src/pageindex_mcp/helpers.py:883`**:

Before:
```python
if node_count >= 3 and depth >= 2 and max_leaf_ratio < 0.15 and not garbled:
```

After:
```python
if node_count >= 3 and depth >= 2 and max_leaf_ratio < 0.17 and not garbled:
```

This aligns the primary PASS gate with `CATEGORY_BC_PROMOTION_THRESHOLD` (0.17), which is already used for cat_b/cat_c promotion at `helpers.py:896` and `helpers.py:903`. The 0.15 vs 0.17 split was an artifact of conservative initial deployment (RFC-014), not a deliberate quality distinction. Doc 19 (`max_leaf_ratio=0.16`) directly benefits.

**Env var.** `PASS_MAX_LEAF_RATIO` (default `0.17`; set `0.15` to restore pre-RFC-021 strictness).

#### QF2c: Small-doc exemption

> **AUDIT CORRECTION (2026-07-28):** Original thresholds (`node_count <= 15`, `max_leaf_ratio < 0.50`) directly break existing guardrail test `test_cat_b_above_017_stays_marginal` (ratio 0.20, 10 nodes, flat prose, 10k chars — all conditions met). Tightened below. **First verify whether QF2b alone rescues doc 8** — if so, QF2c may be deferrable entirely.

**`src/pageindex_mcp/helpers.py:883-884`** -- add a narrowly-scoped PASS path for genuinely small documents:

```python
# After the primary PASS gate (helpers.py:883-884):
# QF2c (RFC-021): small-doc exemption — only for docs that are clearly
# small (few nodes, low leaf ratio) and pass content quality checks.
if (
    not effectively_garbled  # uses QF4 ratio, not binary
    and node_count >= 1
    and node_count <= 10  # tightened from 15 — ~1-2 page heuristic
    and max_leaf_ratio < 0.30  # tightened from 0.50 — excludes ratio=0.20 guardrail
    and content_class.startswith("flat_")
):
    flat_text = _flatten_tree_text(structure)
    if 100 < len(flat_text.strip()) < 15_000:  # ceiling prevents large docs sneaking in
        return "PASS", "small_doc_promoted"
```

**Key differences from original:**
- `max_leaf_ratio < 0.30` (was `< 0.50`) — preserves `test_cat_b_above_017_stays_marginal` (ratio 0.20, which is ≥ 0.17 but < 0.30 is wrong — wait, 0.20 < 0.30 means it would still fire). Let me be more precise: the guardrail test has ratio=0.20, 10 nodes, so with `max_leaf_ratio < 0.20` the test is preserved.
- Actually, tighten to `max_leaf_ratio < 0.20` to explicitly preserve the ratio=0.20 guardrail test.
- `node_count <= 10` (was `<= 15`) — narrower scope.
- `len(flat_text) < 15_000` — upper ceiling (~3 pages) prevents the entire MARGINAL band converting for all small flat docs.
- Uses `effectively_garbled` from QF4 (not binary `garbled`).

**Corrected version:**

```python
if (
    not effectively_garbled
    and node_count >= 1
    and node_count <= 10
    and max_leaf_ratio < 0.20  # preserves test_cat_b_above_017_stays_marginal (ratio=0.20)
    and content_class.startswith("flat_")
):
    flat_text = _flatten_tree_text(structure)
    if 100 < len(flat_text.strip()) < 15_000:
        return "PASS", "small_doc_promoted"
```

**Risk note:** This is deliberately narrow. The `max_leaf_ratio < 0.20` threshold means only docs with well-distributed content qualify. Gated behind `SMALL_DOC_PROMOTION_ENABLED` (default `true`). Existing test `test_cat_b_above_017_stays_marginal` (ratio=0.20, 10 nodes) is preserved because 0.20 is NOT < 0.20.

**Pre-implementation check:** verify whether QF2b alone (0.15→0.17) rescues doc 8. If yes, defer QF2c to reduce risk surface.

### QF3-Fix: Garble Gate Precision for Bilingual Docs

> **AUDIT CORRECTION (2026-07-28):** The original QF3a (markdown-token exclusion) and QF3b (bilingual guard) are **provable NO-OPs** — see root cause analysis above. They are replaced with a diagnosis-first approach.

#### ~~QF3a: Markdown-token exclusion~~ WITHDRAWN

`_LATIN_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")` — markdown tokens (`---`, ` ``` `, `#`) contain no `[A-Za-z]` runs ≥2 chars and can never appear in `latin_tokens`. The proposed `_MD_FORMAT_RE` filter would match zero tokens. Additionally, `classify_verdict` calls `_tree_is_garbled(structure)` without `expected_script`, so the Latin-gibberish prong is inactive at verdict time.

#### ~~QF3b: Bilingual-content guard~~ WITHDRAWN

`_MIXED_SCRIPT_RE` matches ≤8 chars of `[\x21-\x7E]` (no spaces). `latin_segment.split()` yields at most 1 token — the "≥3 common English words" guard can never fire.

#### QF3-D: Diagnosis phase (replaces QF3a + QF3b)

**Phase 3 now starts with diagnosis, not implementation:**

1. Extract doc 17's flattened tree text from its stored `processed/*.json`
2. Run it through each garble sub-check independently:
   - `_is_garbled_blob(text, expected_script=None)` — log each sub-prong result
   - `_has_sparse_mojibake(text)` — log match count, word count, ratio vs 0.02 threshold
   - `_tree_is_garbled(structure)` — log per-node vs full-blob results
3. Identify the firing mechanism and its actual values
4. Design fix based on measured data (threshold calibration, regex refinement, or bilingual content class detection)

**Effort:** 0.25d diagnosis + 0.25-0.5d implementation depending on findings.
**Risk:** None from diagnosis phase; fix design will be reviewed before implementation.

### QF4-Fix: Garble Ratio Check in classify_verdict

> **AUDIT CORRECTION (2026-07-28):** The original 500-char windowing design had three defects: (1) digit-ratio prong requires `len(blob) > 500` so all windows fail it — numeric-junk docs escape detection (وارد 597 class); (2) replacing `_tree_is_garbled` with per-window `_is_garbled_blob` drops `_has_sparse_mojibake` — sparse mojibake docs escape detection (RFC-015 D8 regression); (3) Task 4.2(a) arithmetic was wrong (1/3 windows = 0.33, not 0.03). Corrected below.

**Change.** Keep `_tree_is_garbled` as the binary gate (preserving all existing prongs including `_has_sparse_mojibake`), but add a **ratio overlay** so `classify_verdict` can distinguish "entire document garbled" from "cover page garbled but body clean."

**`src/pageindex_mcp/helpers.py`** -- new helper:

```python
def _garble_ratio(text: str, expected_script: str | None = None) -> float:
    """Return the fraction of text that is garbled.

    Uses two checks in parallel:
    1. Full-text _is_garbled_blob + _has_sparse_mojibake (preserves digit-ratio
       and sparse-mojibake detection that 500-char windows would kill).
    2. Windowed _is_garbled_blob for spatial localization.

    Returns max(full_text_score, window_ratio) so no existing detection
    is weakened (additive-only / HR5-tightening).
    """
    if not text.strip():
        return 1.0

    # Full-text check — preserves digit-ratio (needs len > 500) and sparse mojibake.
    full_garbled = 1.0 if (
        _is_garbled_blob(text, expected_script=expected_script)
        or _has_sparse_mojibake(text)
    ) else 0.0

    # Windowed check — spatial localization for prefix-garbled docs.
    window = 2000  # large enough for digit-ratio prong (> 500)
    chunks = [text[i:i + window] for i in range(0, len(text), window)]
    if not chunks:
        return full_garbled
    garbled_chunks = sum(
        1 for c in chunks
        if _is_garbled_blob(c, expected_script=expected_script)
        or _has_sparse_mojibake(c)
    )
    window_ratio = garbled_chunks / len(chunks)

    return max(full_garbled, window_ratio)
```

**Key design differences from original:**
- Window size is 2000, not 500 — ensures digit-ratio prong (`len(blob) > 500`) can fire within windows.
- Each window runs BOTH `_is_garbled_blob` AND `_has_sparse_mojibake` — preserves RFC-015 D8 sparse detection.
- Full-text check runs in parallel — if the full blob is garbled by ANY prong, ratio ≥ 1.0 regardless of window results.
- `max()` ensures additive-only behavior: any detection that worked before still works.

**`src/pageindex_mcp/helpers.py:881`** -- in `classify_verdict`, add ratio overlay:

```python
garbled = _tree_is_garbled(structure)
# QF4 (RFC-021): a small garbled prefix (cover page OCR noise) should
# not condemn the whole document. Check garble ratio — if < threshold
# of text is garbled, treat as not-garbled for verdict purposes.
# The binary garbled flag is the first gate; the ratio refines it.
flat_text = _flatten_tree_text(structure)
_garble_window_threshold = float(os.environ.get(
    "GARBLE_WINDOW_RATIO_THRESHOLD", "0.05"
))
if garbled:
    garble_pct = _garble_ratio(flat_text)
    effectively_garbled = garble_pct >= _garble_window_threshold
else:
    effectively_garbled = False
# Use effectively_garbled instead of garbled in the PASS gate and
# MARGINAL reason assignment:
if node_count >= 3 and depth >= 2 and max_leaf_ratio < 0.17 and not effectively_garbled:
    return "PASS", ""
```

**Env var note:** Named `GARBLE_WINDOW_RATIO_THRESHOLD` (not `GARBLE_RATIO_THRESHOLD`) to avoid confusion with existing `GARBLE_NODE_RATIO_THRESHOLD` (0.10, different semantics). Read at call-time (not module-level) to match codebase convention (`helpers.py:654-657`).

**Rollback:** `GARBLE_WINDOW_RATIO_THRESHOLD=0.0` makes `effectively_garbled = garbled` — equivalent to pre-QF4 behavior (any garble detected → garbled). This is a true kill switch because the binary `_tree_is_garbled` gate is preserved, not replaced.

**Signature change:** `classify_verdict` does NOT need `expected_script` — `_garble_ratio` inherits `None` from the internal `_tree_is_garbled` call path, matching existing behavior.
...
if effectively_garbled:
    reason = "garbling"
```

**Env var.** `GARBLE_WINDOW_RATIO_THRESHOLD` (default `0.05`; the fraction of 2000-char windows that must be garbled before the whole doc is flagged).

**Note:** `_flatten_tree_text(structure)` is already called later in the function (`helpers.py:890`). Hoist it to avoid double computation.

## Expected Outcomes

### Projected Run 5 Verdict Distribution

| Verdict | Run 4 (actual) | Run 5 (projected) | Delta |
|---|---|---|---|
| PASS | 13 | 19-20 | +6-7 |
| MARGINAL | 9 | 2-3 | -6-7 |
| FAIL | 2 | 2 | 0 |
| ERROR | 1 | 1 | 0 |

### Per-Doc Projections

| Doc | Run 4 | Fix | Run 5 (projected) | Rationale |
|---|---|---|---|---|
| 7 (MOU MOHRE) | MARGINAL | QF1 | PASS | Primary attempt preserves PictureItems, tree restored |
| 8 (Reitlehrer) | MARGINAL | QF2b/QF2c | PASS | Small doc with clean content, threshold relaxed or exempted |
| 13 (Pie chart) | MARGINAL | QF2a | PASS | Image enrichment ratio 2/2 = 1.0, promoted |
| 14 (UAE landscape) | MARGINAL | QF2a | PASS | Image enrichment ratio 4/5 = 0.8, promoted |
| 17 (SLA Agreement) | MARGINAL | QF3 | PASS | Bilingual content no longer flagged as garbled |
| 19 (Data Governance) | MARGINAL | QF2b | PASS | max_leaf_ratio 0.16 < 0.17 new threshold |
| 20 (Labor Exec Regs) | MARGINAL | QF1 + QF4 | PASS | OCR deferral restores tree; garble ratio check ignores cover page noise |
| 21 (Domestic Workers) | MARGINAL | QF1 + QF4 | PASS | Same as doc 20 |
| Remaining 2 MARGINAL | MARGINAL | -- | MARGINAL | Genuine extraction limitations, not threshold bugs |

## Implementation Plan

**Phase 1 -- QF1 OCR deferral (0.5 d).** Modify pre-garble probe to flag-only, remove forced-OCR primary attempt block. Add `PRE_GARBLE_FORCE_OCR_ENABLED` env lever. Unit test: Arabic-filename PDF with corrupt text layer runs primary attempt without force_full_page_ocr, PictureItems preserved.

**Phase 2 -- QF2 threshold tuning (0.75 d).**
- QF2a: Add `image_enrichment_ratio` parameter to `classify_verdict`, compute in `client.py`, add promotion path. Unit test: flat_prose doc with enrichment ratio >= 0.8 -> PASS.
- QF2b: Relax max_leaf_ratio from 0.15 to 0.17. Unit test: doc with max_leaf_ratio=0.16 -> PASS.
- QF2c: Small-doc exemption with env gate. Unit test: 10-node flat doc -> PASS.

**Phase 3 -- QF3 diagnosis + fix (0.5-0.75 d).** Diagnose doc 17's actual garble trigger by running its text through each sub-prong independently. Design and implement fix based on measured data (likely threshold calibration or `_MIXED_SCRIPT_RE` refinement). Unit test: Arabic/English bilingual text -> not garbled. Regression test: genuinely garbled Arabic text still flagged.

**Phase 4 -- QF4 garble ratio (0.5 d).** Implement `_garble_ratio()` with dual full-text + windowed detection (2000-char windows, preserving digit-ratio and sparse-mojibake prongs). Wire into `classify_verdict` with 5% threshold via `GARBLE_WINDOW_RATIO_THRESHOLD` env var. Unit test: prefix-garbled doc -> not effectively garbled. Regression tests: fully numeric-junk text still flagged (digit-ratio preservation); sparse-mojibake text still flagged (RFC-015 D8 preservation).

**Phase 5 -- Full 25-doc corpus reaudit (0.5 d).** Run 5 scorecard vs. projections. Verify zero regressions on Run 4's 13 PASS docs.

**Phase 6 -- QF2a-LT: Dedicated image-file pipeline (1.25 d).** Reconcile with existing `_IMAGE_EXTS` route (`client.py:681`) and its tests. Implement `image_standalone` content class detection in `client.py`, `_classify_image_verdict` in `helpers.py`, image-specific meta fields. Unit tests for all three verdict outcomes (PASS/MARGINAL/FAIL). Integration test with `.jpg` fixture. Add reconciliation task for existing `test_image_blocks.py` / `test_imgblock_audit_findings.py`. Can ship independently after Phase 5 validates QF2a is working.

**Total estimated effort: 3.75 d** (2.75 d quick-fixes + 1 d image pipeline).

### Validation Checkpoints

1. Phase 1: Arabic scanned fixture produces PictureItems on primary attempt (no force_full_page_ocr).
2. Phase 2: Spot-reingest docs 8, 13, 14, 19 -- all PASS with appropriate promotion reasons.
3. Phase 3: Doc 17 reingest -> no garble flag -> PASS.
4. Phase 4: Docs 20, 21 reingest -> verdict_reason is NOT "garbling".
5. Phase 5: Full Run 5 scorecard >= 19 PASS, zero regressions on Run 4 PASS docs.
6. Phase 6: `.jpg` fixture ingested → `content_class=image_standalone`, verdict via `_classify_image_verdict`, no tree metrics in meta. QF2a promotion path still works as fallback when `IMAGE_STANDALONE_PIPELINE_ENABLED=false`.

### Rollback Strategy

Each fix is an isolated commit with env-var kill switches:

| Fix | Rollback lever | Default |
|---|---|---|
| QF1 | `PRE_GARBLE_FORCE_OCR_ENABLED=true` | `false` (fix active) |
| QF2b | `PASS_MAX_LEAF_RATIO=0.15` | `0.17` (relaxed) |
| QF2c | `SMALL_DOC_PROMOTION_ENABLED=false` | `true` (fix active) |
| QF4 | `GARBLE_WINDOW_RATIO_THRESHOLD=0.0` | `0.05` (fix active) |
| QF2a | Git revert only (pure logic, no threshold lever needed) | -- |
| QF3 | N/A — diagnosis phase produces no code changes | -- |

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| QF1: Removing pre-garble OCR wastes one primary attempt on garbled docs | Certain | Low (2-5s per garbled doc) | Fix-3 retry path handles escalation correctly; latency cost is acceptable vs tree collapse |
| QF2b: Relaxing max_leaf_ratio promotes genuinely unbalanced trees to PASS | Low | Medium | Only 0.02 relaxation (0.15->0.17); aligned with existing CATEGORY_BC threshold; full-corpus regression in Phase 5 |
| QF2c: Small-doc exemption promotes low-quality small docs | Medium | Medium | Tightened to `max_leaf_ratio < 0.20`, `node_count <= 10`, char ceiling 15k; gated behind env var; preserves `test_cat_b_above_017_stays_marginal` guardrail |
| QF3: Diagnosis reveals unfixable root cause | Low | Low | Diagnosis phase produces no code changes; fix design reviewed before implementation |
| QF4: Garble ratio windowing weakens detection | Low | High | Dual full-text + windowed design (max of both); full-text check preserves digit-ratio and sparse-mojibake prongs; `GARBLE_WINDOW_RATIO_THRESHOLD=0.0` restores exact pre-QF4 behavior |
| Combined fixes shift Run 4 PASS docs to MARGINAL | Very Low | High | Checkpoint 5 explicitly checks zero regressions; all changes are additive (new promotion paths, relaxed thresholds) -- they cannot demote existing PASS docs |

## Test Plan

| Fix | Test level | Key assertions |
|---|---|---|
| QF1 | Unit + integration | (a) `pre_garbled=True` does NOT invoke `conv_fn(file_path, True)` on primary attempt; (b) PictureItems preserved in primary output; (c) Fix-3 retry path still fires when `validate_tree` returns "garbling"; (d) `PRE_GARBLE_FORCE_OCR_ENABLED=true` restores old behavior; (e) end-to-end: Arabic scanned fixture produces tree with depth >= 2 |
| QF2a | Unit | (a) `flat_prose` + `image_enrichment_ratio=1.0` -> PASS, reason="image_enrichment_promoted"; (b) `image_enrichment_ratio=0.5` -> MARGINAL (below 0.8 threshold); (c) non-flat content_class -> no promotion; (d) `image_enrichment_ratio=None` -> no change |
| QF2b | Unit + regression | (a) `max_leaf_ratio=0.16` + other PASS conditions met -> PASS; (b) `max_leaf_ratio=0.18` -> MARGINAL; (c) `PASS_MAX_LEAF_RATIO=0.15` -> old behavior; (d) existing PASS docs still PASS |
| QF2c | Unit | (a) 10-node, depth=1, clean doc -> PASS, reason="small_doc_promoted"; (b) 20-node doc -> no exemption; (c) garbled small doc -> no exemption; (d) `SMALL_DOC_PROMOTION_ENABLED=false` -> no exemption |
| QF3-D | Diagnosis | Run doc 17 text through each garble sub-prong independently; log which mechanism fires and with what values; design fix based on measurement |
| QF4 | Unit | (a) 2000-char garbled prefix + 18000-char clean body -> 1/10 windows garbled, ratio=0.10, effectively garbled at threshold 0.05; (b) fully numeric-junk text -> full-text digit-ratio fires, ratio=1.0 (regression guard for وارد 597 class); (c) sparse-mojibake text -> full-text `_has_sparse_mojibake` fires, ratio=1.0 (regression guard for RFC-015 D8); (d) `GARBLE_WINDOW_RATIO_THRESHOLD=0.0` restores pre-QF4 binary behavior -> any garble flagged (old behavior) |
| All | Regression | Full 25-doc corpus reaudit: zero regressions on Run 4's 13 PASS docs |

## Open Questions

1. **QF2a data availability.** Does the flat structure carry enrichment flags (`role="image"`, `enriched=True`) at `classify_verdict` call time? If not, the parameter-based approach (passing `image_enrichment_ratio` from `client.py`) is required. Needs code trace confirmation during implementation.
2. **QF2c threshold calibration.** The `node_count <= 15` / `max_leaf_ratio < 0.50` guards are initial estimates. Should they be validated against all 25 corpus docs before landing, or is Phase 5 regression sufficient?
3. **QF3 root cause unknown.** Doc 17's actual garble trigger has never been measured. Phase 3 starts with diagnosis. If the root cause turns out to be `_has_sparse_mojibake` with bilingual content producing too many `_MIXED_SCRIPT_RE` matches, the fix is threshold calibration or regex refinement — NOT the originally proposed `_COMMON_WORDS`/`_MD_FORMAT_RE` approaches (which are provable NO-OPs).
4. **QF4 window size.** Now set to 2000 chars (was 500). This ensures digit-ratio prong (`len(blob) > 500`) can fire within windows. Should it match a page-length heuristic more closely? Validate against corpus.
5. **QF1 test updates.** `tests/test_client_contract.py` D3a block (lines 619-720) asserts force-OCR on garble probe — QF1 inverts this. Must add tasks to update these assertions.
6. **QF2c vs QF2b.** Verify whether QF2b alone (0.15→0.17) rescues doc 8 before implementing QF2c. If yes, defer QF2c.
