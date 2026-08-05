<!-- Space: CITRA -->
<!-- Title: RFC-010: Corpus Gap Remediation — Ingestion Pipeline Hardening -->
<!-- Folder: RFCs -->
<!-- Confluence-Page-Id: 5102862339 -->

---
id: RFC-010
title: Corpus Gap Remediation — Ingestion Pipeline Hardening
status: proposed
date: 2026-07-14
plan-impact: yes
supersedes-decisions-in: []
---

## Traceability

| Artifact | Reference |
|---|---|
| Design Document | [design-rfc010-corpus-gap-remediation.md](../designs/design-rfc010-corpus-gap-remediation.md) |
| Implementation Plan | [tasks-rfc010-corpus-gap-remediation.md](../tasks/tasks-rfc010-corpus-gap-remediation.md) |

## Context

A 25-document corpus gap analysis (2026-07-14, branch `feat/scaling-pageindex`) revealed
6 systemic defects in the ingestion pipeline. Results: 1 PASS (4%), 12 MARGINAL (48%),
12 FAIL (48%). Four gaps are fixable in code (~128 lines total); three are upstream
trade-offs to document and flag. Three documents are stale corpus artifacts that need
only reprocessing (zero code changes).

The defects span two categories:

1. **Missing escalation paths** — image-only PDFs never trigger OCR (Gap 1); garble
   detection is too narrow (Gap 2); the flat-doc path bypasses quality checks entirely.
2. **Heading normalization gaps** — Docling emits indented headings the tree builder
   ignores (Gap 3b); stale pre-Fix-1 artifacts remain in MinIO (Gap 3a, Gap 4).

### What this RFC covers

| Gap | Severity | File:Line | One-liner |
|-----|----------|-----------|-----------|
| 1 | Critical | `client.py:497-528` | Image-only PDFs route to FLAT-03 with no OCR trigger |
| 2 | High | `helpers.py:509-525` | Garble gate misses PUA mojibake, digit-junk, repetition |
| 3a | Critical | MinIO artifacts | 2 docs processed before Fix-1 splitter (stale) |
| 3b | Critical | `converters.py:1048+` | Docling emits indented headings; tree builder ignores them |
| 4 | High | MinIO artifacts | 1 doc processed before Fix-1 NFKC fold (stale) |
| 5a | High | `converters.py:1048+` | Docling في→# substitution bug |
| 6c | Medium | `helpers.py:976-1064` | TOC dot-leader pages misclassified as tables |

### What this RFC does NOT cover

- **Gap 5b** — RTL table word-order reversal (upstream Docling/TableFormer limitation;
  no fix surface in our code). Mitigation: flag `bidi_scrambled: true`.
- **Gap 6a** — Multi-row header flattening (upstream TableFormer; rowspan info lost
  before our pipeline). Mitigation: flag `duplicate_header_ratio`.
- **Gap 6b** — Icon/checkmark cell loss (rasterized glyphs; already flagged via
  `suspected_miss: true`).
- Tree-walk search performance — covered by RFC-009.
- Registry/storage integrity — covered by RFC-007.

## Hard Rule constraints (CLAUDE.md — binding)

- **HR1** — no fix is framed as beating vector RAG on accuracy. All changes improve
  ingestion recall, not retrieval ranking.
- **HR5** — `validate_tree()` continues to run before `save_doc`. The new garble
  heuristics (D3) extend the existing gate, not bypass it. The new flat-path garble
  check (D3 Part B) adds a gate where none existed — strictly tightening, never loosening.
- **HR3** — PII routing is unchanged. OCR escalation (D1) reuses the existing
  `pdf_to_markdown_docling` path which respects `OPENAI_BASE_URL` routing.
- **HR4** — AGPL awareness. All fixes use the existing Docling (MIT) path. No new
  pymupdf dependency is introduced.

## Decision

### D0 — Reprocess stale corpus artifacts (Gap 3a + Gap 4) — immediate, zero code

**Problem.** Three documents were processed before Fix-1 landed (2026-07-01):
- `2030e34d` (Penal Code) — 236k tail-blob, 457 trapped Article markers
- `2a7e0ebe` (Federal Decree-Law 33) — 100k tail-blob, 73 trapped markers
- `ae02da49` (Human Rights) — 319k tail-blob, 116 markers invisible due to
  presentation-form Arabic (U+FExx)

The current splitter (`split_oversized_leaf_nodes` at `helpers.py:797`) and NFKC
fold (`_fold_with_index_map` at `helpers.py:662`) already handle all three cases —
confirmed by running the regex against the raw leaves (425+ matches found). The MinIO
artifacts are simply stale.

**Decision.** Re-run `preprocess_client.py` on these 3 doc_ids. No code changes.

**Caveat.** `ae02da49` (Human Rights) may retain a ~137k residual from genuinely long
individual articles + a ToC block. The 319k→~137k improvement is still significant.

### D1 — Image-ratio pre-check for OCR escalation (Gap 1) — ~35 lines

**Problem.** 6 documents produce completely blank output (0 text chars). Image-only PDFs
yield only `<!-- image -->` blocks from Docling's text-layer extraction. `validate_tree()`
rejects them for `node_count<3`, routing to FLAT-03 — which has zero image-density
awareness. OCR escalation at `client.py:455` is gated on `reason == "garbling"`, making
it structurally unreachable for image-only documents.

**Affected documents:** `073853bd` (100% image, 0 chars), `39959dd7` (100%, 0),
`b604dbaa` (100%, 0), `0fe0aeef` (100%, 0), `55410100` (78%, 58 chars),
`11a82180` (57%, 73 chars).

**Decision.** Insert an image-ratio check at `client.py:497` before FLAT-03 routing:

```python
# At client.py:497, before the existing FLAT-03 block:
if (
    not ok
    and reason in ("node_count<3", "depth<2")
    and ext == ".pdf"
    and _OCR_ESCALATION
    and settings.flat_doc_routing
):
    image_lines = sum(1 for ln in (md_content or "").splitlines() if "<!-- image" in ln)
    total_lines = max(len((md_content or "").splitlines()), 1)
    if image_lines / total_lines > 0.50:
        # Image-dominant PDF with no usable text layer — force OCR retry
        escalation_langs = []
        for src in (detect_ocr_langs(filename), detect_ocr_langs(md_content or "")):
            for lg in src:
                if lg not in escalation_langs:
                    escalation_langs.append(lg)
        langs = await asyncio.to_thread(ensure_tessdata, escalation_langs)
        logger.warning(
            "Image-dominant PDF %s (%d%% image lines); escalating to OCR (lang=%s)",
            filename, int(100 * image_lines / total_lines), langs,
        )
        md_content = await asyncio.to_thread(
            pdf_to_markdown_docling, file_path, True, langs
        )
        # Re-run tree build + splitter + quality gate
        # ... (same pattern as the existing garbling escalation at client.py:455-490)
        OCR_ESCALATION_TOTAL.labels(result="recovered" if ok else "still_image_only").inc()
```

**Rationale.** Reuses all existing infrastructure: `detect_ocr_langs`, `ensure_tessdata`,
`pdf_to_markdown_docling`, `OCR_ESCALATION_TOTAL`. If OCR fails or produces another
low-quality tree, falls through to FLAT-03 as before — no worse than today.

**Kill-switch.** `_OCR_ESCALATION` (existing env var) disables the new path too.
`settings.flat_doc_routing` must also be enabled (existing kill-switch).

### D2 — Heading indent normalization (Gap 3b) — ~30 lines

**Problem.** Docling emits headings with leading whitespace
(`    ### Article (10)`) which CommonMark doesn't recognize as headings. The tree
builder treats them as body text, trapping Article markers in oversized leaf nodes.
4 post-Fix-1 documents affected: `144fbaaf`, `1d682268`, `4806d4bd`, `14f41037`
(21–42k tail-blobs, under the 50k splitter threshold but oversized vs the 514-char
healthy average).

**Decision.** Add a normalization pass in `pdf_to_markdown_docling()` output
(`converters.py:1048+`) that strips leading whitespace before `#` heading markers:

```python
import re
_INDENTED_HEADING_RE = re.compile(r"^[ \t]+(#{1,6}\s)", re.MULTILINE)

def _normalize_indented_headings(md: str) -> str:
    return _INDENTED_HEADING_RE.sub(r"\1", md)
```

Apply after the existing `_relevel_headings` and `_normalize_dashes` post-processing
steps, before returning from `pdf_to_markdown_docling`.

**Rationale.** CommonMark spec requires headings to start at column 0 (or indented
≤3 spaces for ATX). Docling's indentation is a layout artifact, not semantic. Stripping
it is lossless and aligns with the existing heading normalization chain.

### D3 — Extended garble detection (Gap 2) — ~43 lines

**Problem.** `_tree_is_garbled()` at `helpers.py:509` only checks: empty blob, NUL
(`\x00`), replacement char (`U+FFFD`), and control-char ratio >5%. Three corruption
types pass as valid Unicode:

| doc_id | Type | Garble ratio | Why it passes |
|--------|------|-------------|---------------|
| `2c90ef0d` | Font mojibake (PUA + HTML entities) | 21.4% | 0% control chars |
| `4f37b2e3` | Digit-junk ("1651001429"×3481) | 86.3% | Never checked (flat path) |
| `b1a72fb2` | Latin substitution ("آل Oleg") | 2.1% | Localized; keep as MARGINAL |

**Decision (Part A).** Extend `_tree_is_garbled` at `helpers.py:525` (+15 lines):

```python
# After the existing control-char check:
# PUA-char ratio > 3% → garbled (catches font/CMap mojibake)
pua = sum(1 for c in blob if 0xE000 <= ord(c) <= 0xF8FF)
if (pua / len(blob)) > 0.03:
    return True

# Digit ratio > 60% on blobs > 500 chars → garbled (catches numeric junk)
if len(blob) > 500:
    digits = sum(1 for c in blob if c.isdigit())
    if (digits / len(blob)) > 0.60:
        return True

# Single-token repetition > 30% of all words → garbled
words = blob.split()
if words:
    from collections import Counter
    most_common_count = Counter(words).most_common(1)[0][1]
    if (most_common_count / len(words)) > 0.30:
        return True
```

**False-positive safety:** PUA 3% — normal docs have 0% PUA (only broken CMaps produce
PUA). Digit 60% — even the world-stats-pocketbook (heavy numbers) is <30% digits due to
headers/labels. Repetition 30% — normal docs never have one word >5% of total.

**Decision (Part B).** New `_flat_text_is_garbled(md)` at `helpers.py:~975` (+20 lines):
same heuristics applied to flat-path markdown before `route_and_extract_flat`. Wire at
`client.py:~526` — if garbled, override reason to `"garbling"` so OCR escalation can fire.

```python
def _flat_text_is_garbled(md: str) -> bool:
    """Garble check for the flat-doc path (FLAT-03 bypass closure)."""
    blob = md.strip()
    if not blob:
        return True
    if "\x00" in blob or "�" in blob:
        return True
    bad = sum(1 for c in blob if ord(c) < 32 and c not in "\n\r\t")
    if len(blob) > 0 and (bad / len(blob)) > 0.05:
        return True
    pua = sum(1 for c in blob if 0xE000 <= ord(c) <= 0xF8FF)
    if len(blob) > 0 and (pua / len(blob)) > 0.03:
        return True
    if len(blob) > 500:
        digits = sum(1 for c in blob if c.isdigit())
        if (digits / len(blob)) > 0.60:
            return True
    words = blob.split()
    if words:
        from collections import Counter
        most_common_count = Counter(words).most_common(1)[0][1]
        if (most_common_count / len(words)) > 0.30:
            return True
    return False
```

**Note:** `b1a72fb2` (2.1% garble, Latin substitution) should remain MARGINAL, not
rejected — most text is readable Arabic. The thresholds are calibrated to avoid
false-positives on this document.

### D4 — TOC-as-table filter (Gap 6c) — ~20 lines

**Problem.** `621512a9` (World Stats Pocketbook): 265 table blocks, many are actually
TOC/index pages with dot-leader formatting (`Country....83`) misdetected as data tables.

**Decision.** Add a `_looks_like_toc_page` heuristic in `route_and_extract_flat`
(`helpers.py:976`) — check for dot-leader density (`\.{4,}`) and trailing page-number
pattern before committing a block as `role: table`:

```python
_DOT_LEADER_RE = re.compile(r"\.{4,}\s*\d+\s*$")

def _looks_like_toc_page(block_text: str) -> bool:
    lines = block_text.strip().splitlines()
    if len(lines) < 3:
        return False
    dot_lines = sum(1 for ln in lines if _DOT_LEADER_RE.search(ln))
    return (dot_lines / len(lines)) > 0.40
```

Similar pattern to the existing `_looks_like_frontmatter_toc` guard in the splitter.
Blocks matching this heuristic get `role: prose` instead of `role: table`.

### D5 — في→# interim post-process (Gap 5a) — upstream + interim

**Problem.** 2,923 occurrences in `b87e897e` (Federal Decree-Law 33 Arabic). The
high-frequency grammatical particle "في" ("in") is replaced by `#` in Docling's
markdown serialization. Confirmed as a Docling bug: `pdftotext` extracts 162 clean
في and zero `#` from the same PDF.

**Decision.** Two-track fix:

1. **Upstream:** File a Docling issue for the في→# markdown serialization bug.
2. **Interim post-process** (~15 lines in `converters.py`): After
   `pdf_to_markdown_docling` returns, detect Arabic-dominant text (>30% Arabic
   script chars) and replace non-heading-initial `#` with في. Apply only to inline
   `#` (not line-initial `#` which are markdown headings).

```python
_INLINE_HASH_RE = re.compile(r"(?<=\S)#(?=\S)")  # # surrounded by non-whitespace

def _fix_fi_hash_substitution(md: str) -> str:
    arabic_chars = sum(1 for c in md if "؀" <= c <= "ۿ")
    if len(md) > 0 and (arabic_chars / len(md)) > 0.30:
        return _INLINE_HASH_RE.sub("في", md)
    return md
```

**Risk.** This is fragile — the regex may over-correct legitimate inline `#` in Arabic
text. Scoped to P3 priority and guarded by the Arabic-dominance threshold.

**Upstream status (2026-07-15).** Filed as
[docling-project/docling#3802](https://github.com/docling-project/docling/issues/3802).
Maintainer `wittjeff` diagnosed the actual root cause: not the markdown serializer,
but `docling-parse`'s text-extraction fallback. Symbolic/subsetted Arabic fonts assign
the في ligature glyph to a low character code (commonly `0x23`); when the font's
`/ToUnicode` CMap omits that ligature entry (a common PDF-generator bug), docling-parse
fell back to `StandardEncoding[0x23]` = `#`, fabricating ASCII instead of signaling a
miss. Fix PR [docling-parse#299](https://github.com/docling-project/docling-parse/pull/299)
(open, CI green, zero reviews as of 2026-07-15) changes the fallback: unmapped codes in
symbolic or Type0 (composite) fonts now emit a `GLYPH<N>` marker instead of the
fabricated Standard/WinAnsi character. Non-symbolic Latin fonts are unaffected.

**Forward-compat addition (landed ahead of upstream merge, 2026-07-15).** Added a
`"GLYPH<" in blob` check to both `_tree_is_garbled` and `_flat_text_is_garbled` in
`helpers.py` (~2 lines each), so that once docling-parse#299 merges and we bump the
dependency, `GLYPH<N>` markers are treated as garbling and route the document to OCR
escalation instead of silently persisting a marker string. This is additive and
inert today — no PDF in our corpus currently produces `GLYPH<>` output, since our
docling-parse version predates the fix. `_fix_fi_hash_substitution` (D5's interim
regex) remains in place until the upstream fix is confirmed on our corpus; it becomes
dead code once `#` is no longer fabricated (`GLYPH<N>` won't match
`_INLINE_HASH_RE`), at which point it should be removed along with its dedicated tests.

## Implementation Plan

### Batch 0 — Immediate (zero code, ops only)

| Step | Gap | Change | Files |
|------|-----|--------|-------|
| 0.1 | 3a+4 | Re-run `preprocess_client.py` on 3 stale doc_ids: `2030e34d`, `2a7e0ebe`, `ae02da49` | — |
| 0.2 | 3a+4 | Verify tail-blobs split correctly with current splitter code | — |

### Batch 1 — P1 fixes (no dependencies)

| Step | Gap | Change | Files |
|------|-----|--------|-------|
| 1.1 | 1 | Image-ratio OCR pre-check before FLAT-03 routing | `client.py` |
| 1.2 | 6c | TOC dot-leader filter in `route_and_extract_flat` | `helpers.py` |

### Batch 2 — P2 fixes (independent of Batch 1)

| Step | Gap | Change | Files |
|------|-----|--------|-------|
| 2.1 | 2A | Extend `_tree_is_garbled` with PUA/digit/repetition checks | `helpers.py` |
| 2.2 | 2B | New `_flat_text_is_garbled` + client.py wiring | `helpers.py`, `client.py` |
| 2.3 | 3b | Heading indent normalization in `pdf_to_markdown_docling` output | `converters.py` |

### Batch 3 — P3 (complex, upstream-dependent)

| Step | Gap | Change | Files |
|------|-----|--------|-------|
| 3.1 | 5a | File upstream Docling issue | — |
| 3.2 | 5a | Interim في→# post-process for Arabic-dominant text | `converters.py` |

### Batch 4 — Revalidation

| Step | Change | Files |
|------|--------|-------|
| 4.1 | Full 25-doc corpus reprocess via `preprocess_client.py` | — |
| 4.2 | Regenerate `DOC_STORE_CORPUS_REPORT.md` with updated verdicts | — |
| 4.3 | Expected outcome: PASS rate 4%→~60%, FAIL rate 48%→~8% | — |

## Test Strategy

### Gap 1 (D1) — Image-ratio OCR escalation

- Unit test: mock a markdown string with >50% `<!-- image -->` lines. Assert OCR
  escalation fires (`pdf_to_markdown_docling` called with `force_full_page_ocr=True`).
- Unit test: markdown with <50% image lines. Assert FLAT-03 routing proceeds without
  OCR escalation.
- Unit test: `_OCR_ESCALATION=False`. Assert no escalation regardless of image ratio.
- Contract test: verify `OCR_ESCALATION_TOTAL` metric increments with
  `result="recovered"` or `result="still_image_only"`.

### Gap 2 (D3) — Extended garble detection

- Unit test: PUA-heavy string (>3% PUA chars). Assert `_tree_is_garbled` returns True.
- Unit test: digit-junk string (>60% digits, >500 chars). Assert garbled.
- Unit test: single-word repetition (>30%). Assert garbled.
- Unit test: normal German insurance text. Assert NOT garbled (false-positive guard).
- Unit test: `b1a72fb2`-style text (2.1% Latin substitution). Assert NOT garbled.
- Unit test: `_flat_text_is_garbled` with the same cases on raw markdown strings.
- Integration: verify `4f37b2e3` (digit-junk flat path) now triggers garble→OCR
  escalation instead of silent FLAT-03 persistence.

### Gap 3b (D2) — Heading indent normalization

- Unit test: markdown with `    ### Article (10)`. Assert output has `### Article (10)`
  (leading whitespace stripped).
- Unit test: indented code blocks (4+ spaces, no `#`). Assert NOT modified.
- Unit test: `   ## Heading` (3 spaces, valid CommonMark). Assert stripped to
  `## Heading` for consistency.
- Regression: run against the 27-file German insurance corpus. Assert zero heading
  changes (German docs don't exhibit this pattern).

### Gap 6c (D4) — TOC filter

- Unit test: block with >40% dot-leader lines. Assert `_looks_like_toc_page` returns
  True, block classified as `role: prose`.
- Unit test: normal table block. Assert `_looks_like_toc_page` returns False.
- Unit test: short block (<3 lines) with dot leaders. Assert False (too few lines to
  conclude TOC).

### Gap 5a (D5) — في→# post-process

- Unit test: Arabic-dominant text with inline `#`. Assert replaced with في.
- Unit test: non-Arabic text with `#`. Assert no replacement.
- Unit test: line-initial `#` heading markers. Assert NOT replaced.

## Risks

1. **D1 image-ratio threshold (50%) may need tuning.** The 6 affected documents range
   from 57% to 100% image lines. The 50% threshold captures all of them. A lower
   threshold risks false-positives on documents with moderate inline images. If needed,
   the threshold can be raised or made configurable via env var.

2. **D1 OCR escalation doubles processing time.** The OCR retry re-runs
   `pdf_to_markdown_docling` with Tesseract, which is 2.5–6x slower than text-layer
   extraction. For the 6 affected documents, this is acceptable (they currently produce
   zero usable output). The existing `_OCR_ESCALATION` kill-switch provides an escape
   hatch.

3. **D3 garble heuristics may need corpus-specific tuning.** The thresholds (PUA 3%,
   digit 60%, repetition 30%) are calibrated against the current 25-doc corpus and the
   62-file validation set. New document types (e.g., financial spreadsheets with high
   digit density) may require threshold adjustment. All thresholds are constants, not
   env vars — a deliberate choice to avoid config sprawl; they can be promoted to env
   vars if tuning proves necessary.

4. **D3 Part B closes the flat-path bypass but may reject documents that were
   previously accepted.** This is intentional — the flat path currently persists garbled
   text silently (violating HR5's spirit). Documents that fail the new garble check
   either escalate to OCR (if D1 fires) or surface as `low_quality_tree` errors,
   which is the correct behavior per HR5.

5. **D5 في→# regex is fragile.** The inline `#` replacement is a heuristic that may
   over-correct. Scoped to Arabic-dominant text (>30% Arabic chars) and non-heading
   positions only. The upstream Docling fix is the proper resolution; this is an interim
   measure.

6. **D0 reprocessing may not fully resolve `ae02da49` (Human Rights).** Per the 62-file
   validation, this document retains a ~137k residual from genuinely long individual
   articles and a ToC block. The 319k→~137k improvement is still significant, but the
   document may remain MARGINAL rather than PASS.

## Trade-Offs We Live With

These are upstream limitations with no fix surface in our code:

| Issue | Why unfixable | Mitigation |
|-------|--------------|------------|
| **Gap 5b:** RTL table word-order reversal | Docling/TableFormer reads cell text by physical x-coordinate, not bidi-aware | Flag `bidi_scrambled: true` on RTL tables |
| **Gap 6a:** Multi-row header collapse | TableFormer markdown export loses rowspan/colspan info | Flag `duplicate_header_ratio` in quality metadata |
| **Gap 6b:** Icon/checkmark cell loss | Rasterized glyphs have no text layer; tiny icons defeat OCR | Already flagged via `suspected_miss: true` + `empty_cell_ratio` |
| **Gap 5a partial:** Localized Latin substitution | `b1a72fb2` has 2.1% garble; garble gate correctly passes as MARGINAL | Document in quality metadata |

## References

- **Source report:** `DOC_STORE_CORPUS_REPORT.md`, `CORPUS_GAP_ANALYSIS.html`
- **Prior RFCs:** RFC-004 (flat-doc routing), RFC-005 (hard corpus fixes),
  RFC-007/008/009 (landed hardening)
- **Key source files:**
  - `src/pageindex_mcp/client.py` — index orchestration, OCR escalation, flat routing
  - `src/pageindex_mcp/helpers.py` — validate_tree, garble gate, splitter, flat extractor
  - `src/pageindex_mcp/converters.py` — pdf_to_markdown_docling, OCR config, heading recovery
  - `src/pageindex_mcp/storage.py` — save_doc, save_flat_doc, MinIO persistence
- **Memory entries:** `fix1-redesign-and-tessdata-prebake`, `fix2-fix4-table-format-findings`,
  `fix3-ocr-escalation-mojibake-escape`, `rfc004-flat-family-built`,
  `corpus-gap-analysis-2026-07-14`
- **Healthy baseline:** `05ea7b35` (German insurance, 129 nodes / depth 2 / avg leaf 514 chars)
