<!-- Space: CITRA -->
<!-- Title: Tasks: RFC-018 — Corpus Audit Remediation -->
<!-- Folder: Tasks -->

# Tasks: RFC-018 — Corpus Audit Remediation

## Traceability

- RFC: [RFC-018: Corpus Audit Remediation](../rfcs/018-corpus-audit-remediation.md)
  - [Context](../rfcs/018-corpus-audit-remediation.md#context)
  - [What this RFC covers](../rfcs/018-corpus-audit-remediation.md#what-this-rfc-covers)
  - [Out of scope](../rfcs/018-corpus-audit-remediation.md#out-of-scope)
  - [Cross-cutting failure modes from audit](../rfcs/018-corpus-audit-remediation.md#5-cross-cutting-failure-modes-from-audit)
  - [Hard Rule constraints](../rfcs/018-corpus-audit-remediation.md#hard-rule-constraints-claudemd-binding)
  - [User-locked constraints](../rfcs/018-corpus-audit-remediation.md#user-locked-constraints)
  - [Decisions](../rfcs/018-corpus-audit-remediation.md#decisions)
  - [D0 — Fix P0a marker-count mismatch for standalone images](../rfcs/018-corpus-audit-remediation.md#d0--fix-p0a-marker-count-mismatch-for-standalone-images)
  - [D1 — Text-layer availability check before per-picture OCR](../rfcs/018-corpus-audit-remediation.md#d1--text-layer-availability-check-before-per-picture-ocr)
  - [D2 — Arabic RTL reversal hardening](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening)
  - [D3 — Garble-gate numeric-junk probe](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe)
  - [Implementation Plan](../rfcs/018-corpus-audit-remediation.md#implementation-plan)
  - [Test Strategy](../rfcs/018-corpus-audit-remediation.md#test-strategy)
  - [Risks](../rfcs/018-corpus-audit-remediation.md#risks)
  - [Surfaces touched](../rfcs/018-corpus-audit-remediation.md#surfaces-touched)
- Design: [design-rfc018-corpus-audit-remediation.md](../designs/design-rfc018-corpus-audit-remediation.md)
  - [Traceability](../designs/design-rfc018-corpus-audit-remediation.md#traceability)
  - [Overview](../designs/design-rfc018-corpus-audit-remediation.md#overview)
  - [Key Design Principles](../designs/design-rfc018-corpus-audit-remediation.md#key-design-principles)
  - [Launch Constraints](../designs/design-rfc018-corpus-audit-remediation.md#launch-constraints)
  - [Architecture](../designs/design-rfc018-corpus-audit-remediation.md#architecture)
  - [High-Level System Architecture](../designs/design-rfc018-corpus-audit-remediation.md#high-level-system-architecture)
  - [Architecture Decisions](../designs/design-rfc018-corpus-audit-remediation.md#architecture-decisions)
  - [Sequence Diagrams](../designs/design-rfc018-corpus-audit-remediation.md#sequence-diagrams)
  - [Ingestion Flow — D0 / D1 / D3](../designs/design-rfc018-corpus-audit-remediation.md#ingestion-flow--d0--d1--d3)
  - [Arabic Normalization Flow — D2](../designs/design-rfc018-corpus-audit-remediation.md#arabic-normalization-flow--d2)
  - [Service Contracts](../designs/design-rfc018-corpus-audit-remediation.md#service-contracts)
  - [1. client.py](../designs/design-rfc018-corpus-audit-remediation.md#1-clientpy-srcpageindex_mcpclientpy)
  - [2. converters.py](../designs/design-rfc018-corpus-audit-remediation.md#2-converterspy-srcpageindex_mcpconverterspy)
  - [3. helpers.py](../designs/design-rfc018-corpus-audit-remediation.md#3-helperspy-srcpageindex_mcphelperspy)
  - [Correctness Properties](../designs/design-rfc018-corpus-audit-remediation.md#correctness-properties)
  - [Property 1 — marker-count match](../designs/design-rfc018-corpus-audit-remediation.md#property-1-marker-count-match)
  - [Property 2 — text-layer OCR skip](../designs/design-rfc018-corpus-audit-remediation.md#property-2-text-layer-ocr-skip)
  - [Property 3 — Arabic RTL correction](../designs/design-rfc018-corpus-audit-remediation.md#property-3-arabic-rtl-correction)
  - [Property 4 — garble probe escalation](../designs/design-rfc018-corpus-audit-remediation.md#property-4-garble-probe-escalation)
  - [Property 5 — per-node garble detection](../designs/design-rfc018-corpus-audit-remediation.md#property-5-per-node-garble-detection)
  - [Error Handling](../designs/design-rfc018-corpus-audit-remediation.md#error-handling)
  - [Testing Strategy](../designs/design-rfc018-corpus-audit-remediation.md#testing-strategy)
- PRD: [PRD.md](../../PRD.md)
- Architecture: [ARCHITECTURE.md](../../ARCHITECTURE.md)

## Overview

This RFC closes the 8-FAIL / 10-MARGINAL gap surfaced by the 25-doc corpus re-ingestion audit on `feat/image-block-picture-ocr` ([RFC-018 Context](../rfcs/018-corpus-audit-remediation.md#context)). Two batches: **Batch 0** lands four independent code fixes — [RFC-018 D0](../rfcs/018-corpus-audit-remediation.md#d0--fix-p0a-marker-count-mismatch-for-standalone-images) matches synthetic `PictureResult` count to Docling's `<!-- image -->` marker count for standalone images so `splice_figure_markers`' count guard stops discarding chart content; [RFC-018 D1](../rfcs/018-corpus-audit-remediation.md#d1--text-layer-availability-check-before-per-picture-ocr) adds a text-layer probe before per-picture OCR so clean vector text under a chart bbox is never overwritten by garbled Tesseract output; [RFC-018 D2](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening) adds a post-bidi word-order correction pass for Tesseract's LTR-scanned Arabic, which `reconstruct_bidi_order`'s `get_display()` alone cannot fix; and [RFC-018 D3](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe) closes the garble-gate hole with a pre-conversion raw-text-layer probe (D3a) and a per-node garble check inside `validate_tree` (D3b), per [CLAUDE.md HR5](../rfcs/018-corpus-audit-remediation.md#hard-rule-constraints-claudemd-binding). **Batch 1** adds the eleven-assertion test suite from the [RFC Test Strategy](../rfcs/018-corpus-audit-remediation.md#test-strategy) covering all four decisions. All changes are local computation only (Tesseract, fitz, python-bidi) — no new LLM egress, no new derived stores, consistent with [CLAUDE.md HR2-HR4](../rfcs/018-corpus-audit-remediation.md#hard-rule-constraints-claudemd-binding).

## Tasks

- [ ] <a id="1-batch-0--core-fixes-d0-d1-d2-d3"></a>1. Batch 0 — Core Fixes (D0, D1, D2, D3)

  *[RFC Implementation Plan](../rfcs/018-corpus-audit-remediation.md#implementation-plan) Batch 0, Steps 1-6*

  - [ ] <a id="11-fix-marker-count-mismatch-d0"></a>1.1 Fix marker-count mismatch for standalone images ([RFC-018 D0](../rfcs/018-corpus-audit-remediation.md#d0--fix-p0a-marker-count-mismatch-for-standalone-images))

    - In `src/pageindex_mcp/client.py`, in the standalone-image branch (`elif ext in _IMAGE_EXTS:`, currently ~lines 529-545), after `md_content = await asyncio.to_thread(image_to_markdown, file_path, img_langs)`, count the `<!-- image -->` markers actually produced and build a matching-length `pic_results` list instead of the current single-element list:
      ```python
      # D0 (RFC-018): standalone image IS the picture — create N synthetic
      # PictureResults matching the marker count so splice_figure_markers'
      # count guard (marker_count == len(pics)) passes. All N point at the
      # same source image bytes (the standalone image IS every detected
      # sub-region — there is no per-region crop for a flat image file).
      img_bytes = await asyncio.to_thread(Path(file_path).read_bytes)
      marker_count = md_content.count("<!-- image -->")
      pic_results = [PictureResult(
          ocr_text="",
          page=1,
          bbox={"l": 0, "t": 0, "r": 0, "b": 0},
          png_bytes=img_bytes,
      )] * max(1, marker_count)
      ```
    - `max(1, marker_count)` preserves the pre-D0 single-PictureResult behavior when `image_to_markdown` happens to emit zero markers, avoiding an empty `pic_results` list downstream
    - No new imports required — `PictureResult` and `Path` are already imported per RFC-017 D1
    - _Requirements:_ [RFC-018 D0](../rfcs/018-corpus-audit-remediation.md#d0--fix-p0a-marker-count-mismatch-for-standalone-images) | [Design Property 1](../designs/design-rfc018-corpus-audit-remediation.md#property-1-marker-count-match) | [Design Service: client.py](../designs/design-rfc018-corpus-audit-remediation.md#1-clientpy-srcpageindex_mcpclientpy) | [Design Sequence: Ingestion Flow](../designs/design-rfc018-corpus-audit-remediation.md#ingestion-flow--d0--d1--d3)

  - [ ] <a id="12-add-text-layer-check-d1"></a>1.2 Add text-layer check in `_recover_picture_text` Phase 1 loop ([RFC-018 D1](../rfcs/018-corpus-audit-remediation.md#d1--text-layer-availability-check-before-per-picture-ocr))

    - In `src/pageindex_mcp/converters.py`, in `_recover_picture_text`'s Phase 1 loop, immediately after the existing RFC-017 D0 page-coverage `continue` (line ~1389, `if page_area > 0 and (rect.width * rect.height) / page_area > _PICTURE_PAGE_COVERAGE_THRESHOLD: continue`) and before `pix = page.get_pixmap(clip=rect, dpi=300)` (line ~1391), insert:
      ```python
      # D1 (RFC-018): skip per-picture OCR when clean text already exists under
      # the bbox — Docling already extracted it into the markdown body via its
      # text layer; an OCR crop would only replace accurate vector text with
      # garbled/fragmented Tesseract output (audit entries 14-15, RFC-018 F2).
      clip_text = page.get_text("text", clip=rect).strip()
      if len(clip_text) > _PICTURE_OCR_MIN_CHARS:
          continue
      ```
    - Ordering is load-bearing: the D0 area check (RFC-017) must run first so a >60% page region is skipped by the coverage filter regardless of text-layer state — D1 only narrows the remaining <60% region set
    - Reuses `_PICTURE_OCR_MIN_CHARS` (20, already defined at converters.py:1243) as the shared decorative-content threshold rather than introducing a second magic number
    - No new imports — `fitz`/`page` are already in scope in this loop
    - _Requirements:_ [RFC-018 D1](../rfcs/018-corpus-audit-remediation.md#d1--text-layer-availability-check-before-per-picture-ocr) | [Design Property 2](../designs/design-rfc018-corpus-audit-remediation.md#property-2-text-layer-ocr-skip) | [Design Service: converters.py](../designs/design-rfc018-corpus-audit-remediation.md#2-converterspy-srcpageindex_mcpconverterspy) | [Design Sequence: Ingestion Flow](../designs/design-rfc018-corpus-audit-remediation.md#ingestion-flow--d0--d1--d3)

  - [ ] <a id="13-add-arabic-rtl-reversal-hardening-d2"></a>1.3 Add `_fix_residual_rtl_reversal` + `_arabic_readability_score` + `_is_arabic_char` ([RFC-018 D2](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening))

    - In `src/pageindex_mcp/converters.py`, near `reconstruct_bidi_order` (line ~1204-1229, RFC-015 D7), add the three new helpers exactly as specified in the RFC code block:
      - `_is_arabic_char(c: str) -> bool` — codepoint check over U+0600-06FF, U+FB50-FDFF, U+FE70-FEFF
      - `_arabic_readability_score(words: list[str]) -> int` — scores a word list against a conservative ~14-word common-Arabic-function-word set (`_AR_COMMON_WORDS`) plus a definite-article regex (`_AR_DEFINITE_RE`, e.g. `ال`-prefix), `+2` per common word, `+1` per definite-article match
      - `_fix_residual_rtl_reversal(text: str) -> str` — per line: skip if Arabic-char ratio < 50%; otherwise split on whitespace, compute forward vs. reversed readability score, and reverse the word order **only when `rev_score > fwd_score`** (strict `>`, per [RFC-018 Risk 1](../rfcs/018-corpus-audit-remediation.md#risks)); preserve leading/trailing whitespace (`indent`/`trail`) so line structure is untouched
    - Define `_AR_COMMON_WORDS` (frozenset of ~14 high-frequency Arabic function words) and `_AR_DEFINITE_RE` (compiled regex for the `ال`-definite-article prefix) as module-level constants alongside the new functions
    - Pure Python/regex, no new imports, no LLM egress ([CLAUDE.md HR3](../rfcs/018-corpus-audit-remediation.md#hard-rule-constraints-claudemd-binding))
    - _Requirements:_ [RFC-018 D2](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening) | [Design Property 3](../designs/design-rfc018-corpus-audit-remediation.md#property-3-arabic-rtl-correction) | [Design Service: converters.py](../designs/design-rfc018-corpus-audit-remediation.md#2-converterspy-srcpageindex_mcpconverterspy) | [Design Sequence: Arabic Normalization Flow](../designs/design-rfc018-corpus-audit-remediation.md#arabic-normalization-flow--d2)

  - [ ] <a id="14-call-rtl-reversal-in-pre-inference-normalize-d2"></a>1.4 Call `_fix_residual_rtl_reversal` in `_pre_inference_normalize` ([RFC-018 D2](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening))

    - In `src/pageindex_mcp/converters.py`, in `_pre_inference_normalize()` (line ~1492), immediately after the existing `text = reconstruct_bidi_order(text)` call (line ~1501, RFC-015 D7), add:
      ```python
      text = reconstruct_bidi_order(text)  # D7
      text = _fix_residual_rtl_reversal(text)  # D2 (RFC-018)
      ```
    - Ordering is load-bearing: `_fix_residual_rtl_reversal` must run **after** `reconstruct_bidi_order` — it corrects a residual corruption pattern (Tesseract LTR-scanned Arabic) that `get_display()`'s visual-to-logical reordering does not fully resolve, not a replacement for it
    - Depends on [Task 1.3](#13-add-arabic-rtl-reversal-hardening-d2) — the helper functions must exist before this call site is added
    - _Requirements:_ [RFC-018 D2](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening) | [Design Property 3](../designs/design-rfc018-corpus-audit-remediation.md#property-3-arabic-rtl-correction) | [Design Sequence: Arabic Normalization Flow](../designs/design-rfc018-corpus-audit-remediation.md#arabic-normalization-flow--d2)

  - [ ] <a id="15-add-pre-conversion-garble-probe-d3a"></a>1.5 Add pre-conversion text-layer garble probe in `index()` PDF branch ([RFC-018 D3](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe))

    - In `src/pageindex_mcp/client.py`, in the PDF branch of `index()` (`if ext == ".pdf":`, line ~413), before the `chain = pdf_markdown_converters()` converter loop begins, add a non-fatal probe over the raw first-page text layer:
      ```python
      # D3a (RFC-018): pre-conversion text-layer probe. If the raw PDF text
      # layer is garbled, skip straight to force_full_page_ocr=True instead of
      # wasting a non-OCR conversion attempt that will just fail validate_tree
      # downstream (RFC-018 F4).
      pre_garbled = False
      try:
          import fitz  # PyMuPDF, AGPL-3.0 — already a transitive dep (HR4)

          with fitz.open(file_path) as probe_pdf:
              if probe_pdf.page_count > 0:
                  raw_text = probe_pdf[0].get_text()
                  if raw_text.strip() and _flat_text_is_garbled(raw_text):
                      pre_garbled = True
                      logger.info(
                          "D3a: raw text layer garbled for %s, forcing full-page "
                          "OCR upfront",
                          filename,
                      )
      except Exception:
          pass  # probe failure is non-fatal — fall through to the normal chain
      ```
    - **Deviation from the RFC code sketch, reconciled with the actual converter architecture:** the RFC sketch calls `pdf_to_markdown_docling(file_path, True/False, ocr_langs)` directly, but `index()` actually iterates the `chain = pdf_markdown_converters()` list of `(name, fn)` pairs, and only `pdf_to_markdown_docling` accepts a `force_full_page_ocr` kwarg — `_pdf_to_markdown_no_pics` (the pymupdf4llm entry) does not. Implement by rewriting the `docling` entry in `chain` with `functools.partial(pdf_to_markdown_docling, force_full_page_ocr=True)` when `pre_garbled` is true, before the existing `for idx, (conv_name, conv_fn) in enumerate(chain):` loop runs, leaving the pymupdf4llm entry unmodified
    - Use `_flat_text_is_garbled` (already imported from `.helpers` at the top of `client.py`) rather than importing `_is_garbled_blob` directly, keeping a single public garble-check entry point per module
    - `fitz` import is local/guarded per [RFC-018 Risk 2](../rfcs/018-corpus-audit-remediation.md#risks) — only fires for PDF files, non-fatal on failure
    - _Requirements:_ [RFC-018 D3](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe) | [Design Property 4](../designs/design-rfc018-corpus-audit-remediation.md#property-4-garble-probe-escalation) | [Design Service: client.py](../designs/design-rfc018-corpus-audit-remediation.md#1-clientpy-srcpageindex_mcpclientpy) | [Design Sequence: Ingestion Flow](../designs/design-rfc018-corpus-audit-remediation.md#ingestion-flow--d0--d1--d3)

  - [ ] <a id="16-add-per-node-garble-check-d3b"></a>1.6 Add `_garble_check_nodes` helper + per-node garble check ([RFC-018 D3](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe))

    - In `src/pageindex_mcp/helpers.py`, add `import os` to the module's top-of-file imports (not currently imported) for the new env-var threshold
    - Add a recursive per-node counter near `_is_garbled_blob` (line ~567) and `_tree_is_garbled` (line ~633):
      ```python
      def _garble_check_nodes(nodes: list[dict]) -> int:
          """Count nodes with individually-garbled text (D3b, RFC-018).

          Complements the bulk flattened-text check in _tree_is_garbled: a
          single mojibake/PUA-heavy node diluted across dozens of clean nodes
          escapes bulk ratio detection but is caught here per-node.
          """
          garbled = 0
          for node in nodes:
              text = node.get("text", "")
              if text.strip() and _is_garbled_blob(text):
                  garbled += 1
              children = node.get("nodes") or []
              garbled += _garble_check_nodes(children)
          return garbled
      ```
    - Add the configurable ratio threshold as a module-level constant, following the same `os.getenv` pattern as `_PICTURE_PAGE_COVERAGE_THRESHOLD` in `converters.py` ([RFC-018 Risk 4](../rfcs/018-corpus-audit-remediation.md#risks)):
      ```python
      _GARBLE_NODE_RATIO_THRESHOLD = float(os.getenv("GARBLE_NODE_RATIO_THRESHOLD", "0.10"))
      ```
    - Wire into `validate_tree()` (line ~638) as a new check, ordered **after** the existing `_tree_is_garbled` check and **before** `_tree_is_reordered`, so it is additive to (never narrows) the existing gate priority order, consistent with [CLAUDE.md HR5](../rfcs/018-corpus-audit-remediation.md#hard-rule-constraints-claudemd-binding):
      ```python
      total_nodes = _tree_node_count(structure)
      if total_nodes > 0 and (_garble_check_nodes(structure) / total_nodes) > _GARBLE_NODE_RATIO_THRESHOLD:
          return False, "node_garbling"
      ```
    - The new `"node_garbling"` reason string surfaces through the existing `ok, reason = validate_tree(...)` → `raise LowQualityTreeError(reason)` call chain in `client.py` (line ~890) unchanged — no new error-handling path needed
    - _Requirements:_ [RFC-018 D3](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe) | [Design Property 5](../designs/design-rfc018-corpus-audit-remediation.md#property-5-per-node-garble-detection) | [Design Service: helpers.py](../designs/design-rfc018-corpus-audit-remediation.md#3-helperspy-srcpageindex_mcphelperspy) | [CLAUDE.md HR5](../rfcs/018-corpus-audit-remediation.md#hard-rule-constraints-claudemd-binding)

  - [ ] <a id="17-checkpoint--batch-0"></a>1.7 Checkpoint — Batch 0

    - Confirm `client.py`, `converters.py`, and `helpers.py` all still import cleanly (`uv run python -c "import src.pageindex_mcp.client, src.pageindex_mcp.converters, src.pageindex_mcp.helpers"` or equivalent)
    - Confirm no name collisions: `_fix_residual_rtl_reversal`/`_arabic_readability_score`/`_is_arabic_char` ([Task 1.3](#13-add-arabic-rtl-reversal-hardening-d2)) don't shadow existing converters.py symbols; `_garble_check_nodes`/`_GARBLE_NODE_RATIO_THRESHOLD` ([Task 1.6](#16-add-per-node-garble-check-d3b)) don't shadow existing helpers.py symbols
    - Manually trace the ordering-sensitive call sites once each: `_pre_inference_normalize` calls `reconstruct_bidi_order` then `_fix_residual_rtl_reversal` ([Task 1.4](#14-call-rtl-reversal-in-pre-inference-normalize-d2)); `_recover_picture_text` Phase 1 checks page-coverage (RFC-017 D0) then text-layer (D1, [Task 1.2](#12-add-text-layer-check-d1)); `validate_tree` checks node_count → depth → bulk garbling → node garbling ([Task 1.6](#16-add-per-node-garble-check-d3b)) → reordered
    - _Requirements:_ [RFC-018 D0](../rfcs/018-corpus-audit-remediation.md#d0--fix-p0a-marker-count-mismatch-for-standalone-images) | [RFC-018 D1](../rfcs/018-corpus-audit-remediation.md#d1--text-layer-availability-check-before-per-picture-ocr) | [RFC-018 D2](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening) | [RFC-018 D3](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe)

- [ ] <a id="2-batch-1--tests-d0-d1-d2-d3"></a>2. Batch 1 — Tests (D0, D1, D2, D3)

  *[RFC Implementation Plan](../rfcs/018-corpus-audit-remediation.md#implementation-plan) Batch 1, Steps 7-13 · [RFC Test Strategy](../rfcs/018-corpus-audit-remediation.md#test-strategy)*

  - [ ] <a id="21-test-marker-count-match-d0"></a>2.1 Test: standalone image marker-count match ([RFC-018 D0](../rfcs/018-corpus-audit-remediation.md#d0--fix-p0a-marker-count-mismatch-for-standalone-images))

    - In `tests/test_image_blocks.py`, add `test_standalone_image_marker_count_match`: mock `image_to_markdown` to return markdown containing 3 `<!-- image -->` markers, assert the resulting `pic_results` has exactly 3 entries and every entry shares the same `png_bytes`
    - Add `test_standalone_image_single_marker`: mock `image_to_markdown` to return markdown with 1 marker, assert exactly 1 `PictureResult` (no regression vs. RFC-017 D1's pre-D0 behavior)
    - **Validates:** [Design Property 1](../designs/design-rfc018-corpus-audit-remediation.md#property-1-marker-count-match) | [RFC-018 D0](../rfcs/018-corpus-audit-remediation.md#d0--fix-p0a-marker-count-mismatch-for-standalone-images) | [RFC Test Strategy](../rfcs/018-corpus-audit-remediation.md#test-strategy) rows `test_standalone_image_marker_count_match`, `test_standalone_image_single_marker`

  - [ ] <a id="22-test-text-layer-skip-d1"></a>2.2 Test: text-layer check skips per-picture OCR ([RFC-018 D1](../rfcs/018-corpus-audit-remediation.md#d1--text-layer-availability-check-before-per-picture-ocr))

    - In `tests/test_image_blocks.py`, add `test_text_layer_skips_picture_ocr`: construct a region whose bbox overlaps a page area with >20 chars of extractable text (via a fitz-backed fixture PDF or a mocked `page.get_text`), assert the region index is **not** present in the `crops` dict returned by `_recover_picture_text`'s Phase 1 loop
    - Add `test_text_layer_check_with_area_filter`: construct a region covering >60% of page area **and** with text under the bbox, assert it is skipped by the existing RFC-017 D0 area check (i.e. never reaches the D1 text-layer probe) — confirms area-check precedence established in [Task 1.2](#12-add-text-layer-check-d1)
    - **Validates:** [Design Property 2](../designs/design-rfc018-corpus-audit-remediation.md#property-2-text-layer-ocr-skip) | [RFC-018 D1](../rfcs/018-corpus-audit-remediation.md#d1--text-layer-availability-check-before-per-picture-ocr) | [RFC Test Strategy](../rfcs/018-corpus-audit-remediation.md#test-strategy) rows `test_text_layer_skips_picture_ocr`, `test_text_layer_check_with_area_filter`

  - [ ] <a id="23-test-text-layer-allow-d1"></a>2.3 Test: text-layer check allows per-picture OCR when clip is empty ([RFC-018 D1](../rfcs/018-corpus-audit-remediation.md#d1--text-layer-availability-check-before-per-picture-ocr))

    - In `tests/test_image_blocks.py`, add `test_no_text_layer_allows_picture_ocr`: construct a region under 60% page area with an empty/near-empty text clip (≤20 chars), assert the region index **is** present in the `crops` dict — proves D1 does not regress the legitimate scanned-chart OCR path
    - **Validates:** [Design Property 2](../designs/design-rfc018-corpus-audit-remediation.md#property-2-text-layer-ocr-skip) | [RFC-018 D1](../rfcs/018-corpus-audit-remediation.md#d1--text-layer-availability-check-before-per-picture-ocr) | [RFC Test Strategy](../rfcs/018-corpus-audit-remediation.md#test-strategy) row `test_no_text_layer_allows_picture_ocr`

  - [ ] <a id="24-test-reversed-arabic-fixed-d2"></a>2.4 Test: reversed Arabic word order detected and corrected ([RFC-018 D2](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening))

    - In `tests/test_rfc010_converters.py`, add `test_reversed_arabic_word_order_fixed`: call `_fix_residual_rtl_reversal("دراوملا ةرازو")`, assert the output equals `"وزارة الموارد"` (correct reading order) — the exact fixture from [RFC-018 D2 Problem](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening)
    - **Validates:** [Design Property 3](../designs/design-rfc018-corpus-audit-remediation.md#property-3-arabic-rtl-correction) | [RFC-018 D2](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening) | [RFC Test Strategy](../rfcs/018-corpus-audit-remediation.md#test-strategy) row `test_reversed_arabic_word_order_fixed`

  - [ ] <a id="25-test-correct-arabic-unchanged-d2"></a>2.5 Test: correct Arabic and mixed-script text left unchanged ([RFC-018 D2](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening))

    - In `tests/test_rfc010_converters.py`, add `test_correct_arabic_unchanged`: call `_fix_residual_rtl_reversal("وزارة الموارد")` (already-correct order), assert the output is byte-for-byte unchanged — proves `rev_score > fwd_score` strict-inequality gating from [RFC-018 Risk 1](../rfcs/018-corpus-audit-remediation.md#risks) prevents false-positive reversal
    - Add `test_mixed_arabic_latin_preserved`: call `_fix_residual_rtl_reversal` on a line with <50% Arabic-character ratio (e.g. a German sentence containing one Arabic loanword), assert unchanged — proves the 50% line-level gate in [Task 1.3](#13-add-arabic-rtl-reversal-hardening-d2) prevents the heuristic from firing on non-Arabic content
    - **Validates:** [Design Property 3](../designs/design-rfc018-corpus-audit-remediation.md#property-3-arabic-rtl-correction) | [RFC-018 D2](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening) | [RFC-018 Risk 1](../rfcs/018-corpus-audit-remediation.md#risks) | [RFC Test Strategy](../rfcs/018-corpus-audit-remediation.md#test-strategy) rows `test_correct_arabic_unchanged`, `test_mixed_arabic_latin_preserved`

  - [ ] <a id="26-test-garble-probe-numeric-junk-d3a"></a>2.6 Test: pre-conversion garble probe triggers on numeric-junk text layer ([RFC-018 D3](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe))

    - In `tests/test_client_contract.py`, add `test_garble_probe_numeric_junk`: construct/mock a PDF whose first-page raw text layer is 89% digits (matching the وارد 597 audit fixture, [RFC-018 D3 Evidence](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe)), assert the docling chain entry is invoked with `force_full_page_ocr=True` on the first attempt (per the `functools.partial` rewrite in [Task 1.5](#15-add-pre-conversion-garble-probe-d3a)) — i.e. no wasted non-OCR attempt
    - Add `test_garble_probe_clean_text`: construct/mock a PDF with a clean first-page text layer, assert the docling chain entry is invoked with `force_full_page_ocr=False` (normal path, `pre_garbled` stays `False`)
    - **Validates:** [Design Property 4](../designs/design-rfc018-corpus-audit-remediation.md#property-4-garble-probe-escalation) | [RFC-018 D3](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe) | [RFC Test Strategy](../rfcs/018-corpus-audit-remediation.md#test-strategy) rows `test_garble_probe_numeric_junk`, `test_garble_probe_clean_text`

  - [ ] <a id="27-test-per-node-garble-catches-pua-d3b"></a>2.7 Test: per-node garble check catches a PUA-heavy node in an otherwise-clean tree ([RFC-018 D3](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe))

    - In `tests/test_storage_meta.py`, add `test_per_node_garble_catches_pua_node`: build a 100-node tree fixture (mirroring the القرار التنظيمي audit case — 99 clean nodes + 1 PUA-heavy mojibake node, [RFC-018 D3 Evidence](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe)), call `_garble_check_nodes` directly and assert it returns `1`; then call `validate_tree` on the same fixture and assert it returns `(False, "node_garbling")` — the 1/100 = 1% ratio is below the bulk `_tree_is_garbled` threshold but exceeds neither; confirm the 10% `_GARBLE_NODE_RATIO_THRESHOLD` default from [Task 1.6](#16-add-per-node-garble-check-d3b) needs the fixture's ratio tuned above 10% (e.g. 11+ mojibake nodes among 100) for `validate_tree` to actually reject — assert both the raw count (`_garble_check_nodes` = expected count) and the threshold-gated `validate_tree` outcome at that ratio
    - **Validates:** [Design Property 5](../designs/design-rfc018-corpus-audit-remediation.md#property-5-per-node-garble-detection) | [RFC-018 D3](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe) | [RFC-018 Risk 4](../rfcs/018-corpus-audit-remediation.md#risks) | [RFC Test Strategy](../rfcs/018-corpus-audit-remediation.md#test-strategy) row `test_per_node_garble_catches_pua_node`

  - [ ] <a id="28-checkpoint--batch-1"></a>2.8 Checkpoint — Batch 1

    - Run `uv run pytest tests/test_image_blocks.py tests/test_rfc010_converters.py tests/test_client_contract.py tests/test_storage_meta.py -q` and confirm all 11 new tests ([Task 2.1](#21-test-marker-count-match-d0) through [Task 2.7](#27-test-per-node-garble-catches-pua-d3b)) pass alongside the existing suite
    - Run the full suite (`uv run pytest`) to confirm no regression in `validate_tree`'s existing `node_count<3` / `depth<2` / `garbling` / `reordered` reasons from adding the new `"node_garbling"` check ([Task 1.6](#16-add-per-node-garble-check-d3b))
    - Confirm the RFC's [Test Strategy](../rfcs/018-corpus-audit-remediation.md#test-strategy) table is fully covered — 2 D0 rows + 3 D1 rows + 3 D2 rows + 2 D3a rows + 1 D3b row, 11 total assertions
    - _Requirements:_ [RFC-018 D0](../rfcs/018-corpus-audit-remediation.md#d0--fix-p0a-marker-count-mismatch-for-standalone-images) | [RFC-018 D1](../rfcs/018-corpus-audit-remediation.md#d1--text-layer-availability-check-before-per-picture-ocr) | [RFC-018 D2](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening) | [RFC-018 D3](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe) | [RFC Test Strategy](../rfcs/018-corpus-audit-remediation.md#test-strategy)

## Notes

- **D0** ([RFC-018 D0](../rfcs/018-corpus-audit-remediation.md#d0--fix-p0a-marker-count-mismatch-for-standalone-images)) fixes RFC-017 D1's marker-count mismatch — the count guard at `splice_figure_markers` (`converters.py:1462-1470`) was silently bailing on standalone images because `marker_count (N) != len(pics) (1)`, discarding all chart content back to bare `<!-- image -->` strings (audit finding F1, entry 13).
- **D1** ([RFC-018 D1](../rfcs/018-corpus-audit-remediation.md#d1--text-layer-availability-check-before-per-picture-ocr)) closes RFC-017 D0's remaining gap: the >60% page-coverage area filter alone does not protect sub-60% chart regions that sit on top of a clean embedded text layer (audit finding F2, entries 14-15) — those still had per-picture OCR overwrite accurate vector text with garbled Tesseract output.
- **D2** ([RFC-018 D2](../rfcs/018-corpus-audit-remediation.md#d2--arabic-rtl-reversal-hardening)) is a heuristic, not a guarantee — see [RFC-018 Risk 1](../rfcs/018-corpus-audit-remediation.md#risks). The strict `rev_score > fwd_score` gate and conservative 14-word common-word set are deliberate false-positive mitigations; mixed-direction lines (Arabic + Latin/digits) are an accepted known limitation requiring Docling-level Tesseract `--psm` tuning as a future fix, not in scope here.
- **D3a** ([RFC-018 D3](../rfcs/018-corpus-audit-remediation.md#d3--garble-gate-numeric-junk-probe)) requires a local, guarded `fitz` import inside `client.py` — see [RFC-018 Risk 2](../rfcs/018-corpus-audit-remediation.md#risks). `fitz` (PyMuPDF, AGPL-3.0) is already a transitive dependency via `pymupdf4llm`/`docling` ([CLAUDE.md HR4](../rfcs/018-corpus-audit-remediation.md#hard-rule-constraints-claudemd-binding)), so this adds no new AGPL exposure.
- **D1's 20-char threshold** has a known false-negative edge case per [RFC-018 Risk 3](../rfcs/018-corpus-audit-remediation.md#risks): a text layer that passes the char-count check but is itself garbled/mojibake will still be skipped from per-picture OCR. This is accepted — D3a/D3b's garble-gate escalation is the downstream backstop, not D1's job.
- **D3b's threshold** ([RFC-018 Risk 4](../rfcs/018-corpus-audit-remediation.md#risks)) is deliberately made configurable via `GARBLE_NODE_RATIO_THRESHOLD` (default `0.10`) rather than hardcoded, since documents with legitimate numeric-heavy nodes (financial tables) may need a higher tolerance — tune based on corpus validation, not in this RFC's scope.
- All four decisions comply with [CLAUDE.md HR2](../rfcs/018-corpus-audit-remediation.md#hard-rule-constraints-claudemd-binding) (no new derived stores — D0 reuses the existing `figures/<doc_id>/` prefix; D2/D3 are in-place text transforms) and [CLAUDE.md HR3](../rfcs/018-corpus-audit-remediation.md#hard-rule-constraints-claudemd-binding) (no new LLM egress — D0/D1 use local Tesseract only, D2 is pure Unicode computation, D3 uses local `fitz` text extraction).

## Task Dependency Graph

```json
{
  "waves": [
    {
      "wave": 1,
      "tasks": ["1.1", "1.2", "1.3", "1.5", "1.6"]
    },
    {
      "wave": 2,
      "tasks": ["1.4"]
    },
    {
      "wave": 3,
      "tasks": ["1.7"]
    },
    {
      "wave": 4,
      "tasks": ["2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7"]
    },
    {
      "wave": 5,
      "tasks": ["2.8"]
    }
  ]
}
```
