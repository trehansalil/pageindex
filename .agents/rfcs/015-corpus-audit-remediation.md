<!-- Space: CITRA -->
<!-- Title: RFC-015: Corpus Audit Remediation — Verdict Engine & Extraction Gaps -->
<!-- Folder: RFCs -->

---
id: RFC-015
title: Corpus Audit Remediation — Verdict Engine & Extraction Gaps
status: proposed
date: 2026-07-17
plan-impact: yes
supersedes-decisions-in: []
---

## Context

A 26-file corpus audit (2026-07-17, branch `feat/scaling-pageindex`) independently
re-derived every stored verdict from the persisted MinIO `processed/*.meta.json`
artifacts. Results: **11 PASS / 12 MARGINAL / 2 FAIL / 1 NOT_PROCESSED**. This
supersedes a prior fabricated report that claimed 15/10/0. Full diagnostic:
`DOC_STORE_CORPUS_REPORT.md`.

The audit uncovered defects in two layers that RFC-010 did not address:

1. **Verdict engine correctness** — `classify_verdict()` has no ordering or
   nesting-sanity check; two stored PASS verdicts are independently confirmed
   wrong. This directly threatens **Hard Rule 5** (never silently persist a
   low-quality tree).
2. **Extraction & normalization gaps** — marker leakage, new tail-blob sub-causes,
   chart text loss, BiDi scrambling, sparse mojibake, and table-parser edge cases.

### Relationship to prior RFCs

| RFC | What it covered | What this RFC adds |
|-----|----------------|-------------------|
| RFC-005 | Fix-1 splitter redesign (NFKC fold, inline match, longest-run guard) | New tail-blob sub-causes the redesigned splitter still misses (size gate, `Schedule` regex, run-together headings, letter-suffix promotion) |
| RFC-010 D3 | Garble gate extension (PUA, digit-junk, repetition) | Sparse mixed-script detection (localized Latin fragments adjacent to Arabic) |
| RFC-010 D5 | في→# interim regex (`_INLINE_HASH_RE`) | Widened regex to consume whole `#+` runs + pipeline reordering to run before heading-depth inference |
| RFC-010 Gap 5b | RTL table word-order flagged as upstream trade-off | Promoted to fixable — `python-bidi` per-line normalization, gated on Arabic-ratio threshold |

### What this RFC covers

| ID | Pri | File:Line | One-liner |
|----|-----|-----------|-----------|
| D1 | P0 | `preprocess_client.py:111` | Batch tool drops `.jpg`/`.xlsx` silently |
| D2 | P0 | `helpers.py:594-694` | PASS despite confirmed content reordering (`54e92c0a`) |
| D3 | P0 | `helpers.py:611-628`, `converters.py:202-270` | PASS despite 3.5–6× ratio mismatch + staircase nesting (`a4c1b522`) |
| D4 | P1 | `converters.py:1076-1087` | `#في#`/`#فيفي#` marker leakage breaks splitter (5 docs) |
| D5 | P1 | `helpers.py:806-813,974-1043`, `converters.py:647-650` | Giant-tail-blob heading-boundary miss — 4 distinct sub-causes (6+ docs) |
| D6 | P1 | `converters.py:1090-1235` | Chart text swallowed by image bbox (2 docs) |
| D7 | P1 | `converters.py` (new) | RTL/BiDi word-order scramble (2 docs) |
| D8 | P1 | `helpers.py:554-587` | Mojibake evades garble gate via sparse mixed-script (3 docs) |
| D9 | P1 | `helpers.py:771-786` | Rowspan forward-fill inconsistency (`e544d939`) |
| D10 | P1 | `page_index_md.py:32-57` | Preamble content dropped before first heading (`722eb392`) |

### What this RFC does NOT cover

- **P2 issues** requiring further investigation: icon/checkmark cell loss
  (`460e3c7d`), duplicated tail nodes at EOF (`67a9f5d2`), partial table-grid
  reconstruction breakdown (`e6c2e8c6` — 43/232 pages). These need deeper
  diagnostics before a fix can be scoped.
- **P3 trade-offs**: single-page documents correctly flagged FAIL by design;
  `max_leaf_ratio`-only quality gates as a structural limitation (see
  DOC_STORE_CORPUS_REPORT.md § Known Trade-offs).
- Performance and query-path optimizations — covered by RFC-009.
- Registry/storage integrity — covered by RFC-007.

## Hard Rule constraints (CLAUDE.md — binding)

- **HR1** — no fix is framed as beating vector RAG on accuracy. All changes
  improve ingestion fidelity, not retrieval ranking.
- **HR5** — `validate_tree()` continues to run before `save_doc`. D2 and D3 add
  new checks (ordering, nesting sanity) to `validate_tree` and `classify_verdict`
  — strictly tightening the gate, never loosening. Documents that previously
  received a wrong PASS will now correctly receive MARGINAL or trigger re-extraction.
- **HR3** — PII routing unchanged. OCR escalation (D6, D8) reuses the existing
  `pdf_to_markdown_docling` path which respects `OPENAI_BASE_URL` routing.
- **HR4** — AGPL awareness. D7's `python-bidi` dependency is MIT-licensed. No new
  pymupdf dependency is introduced.

## Decision

### D1 — Batch tooling: unify SUPPORTED set (`P0`, ~5 lines)

**Problem.** `preprocess_client.py:111` maintains its own `SUPPORTED = {".pdf",
".docx", ".pptx", ".md", ".txt", ".html"}`, excluding `.jpg` and `.xlsx` even
though `client.py:63-64` (`_IMAGE_EXTS`/`_SUPPORTED`) already handles both via
working OCR and conversion routes. The file
`image pie chart about labor distribution in january 2025 - Copy.jpg` was silently
excluded — no job, no log, no error.

**Decision.** Import `_SUPPORTED` from `client.py` and remove the duplicate set:

```python
# preprocess_client.py — replace the hardcoded SUPPORTED set
from pageindex_mcp.client import _SUPPORTED as SUPPORTED
```

**Rationale.** Zero new code path — the OCR route for images already exists and
works via the HTTP upload path. This is a pure config-drift fix.

### D2 — Verdict engine: content-ordering check (`P0`, ~25 lines)

**Problem.** `54e92c0a` (Federal Decree-Law 47/2021) stores a PASS verdict, but a
~2-page span (Article 9 clauses, physically page 9) is emitted *after* Article 13
(physically pages 12-13). Node `0062` (Article 13) has `line_num=114`; node `0063`
(Parental leave) has `line_num=122`. `max_leaf_ratio` and garbling checks are both
blind to this class of defect. Article 12's heading is also dropped entirely.

**Decision.** Add `_tree_is_reordered()` — walk the tree and flag any node whose
`start_index` (or `line_num` as proxy) regresses below the running max:

```python
def _tree_is_reordered(tree: dict) -> bool:
    """Detect content emitted out of document order."""
    max_seen = -1
    for node in _walk_leaves(tree):
        idx = node.get("start_index", node.get("line_num", 0))
        if idx < max_seen:
            return True
        max_seen = max(max_seen, idx)
    return False
```

Wire into:
- `validate_tree` (`helpers.py:594-606`) — reject pre-`save_doc` per HR5
- `classify_verdict` (`helpers.py:650-694`) — force below PASS, surface
  `"reordered"` in the reason string

**Rationale.** Content reordering is invisible to size-based metrics but
fundamentally breaks document fidelity. A reordered tree should never receive PASS.

### D3 — Verdict engine: ratio denominator fix + English heading labels (`P0`, ~30 lines)

**Problem.** `a4c1b522` (Ministerial Resolution 279/2022) stores `max_leaf_ratio=0.0971`;
independently recomputed at 0.34–0.61. Root cause: `_tree_max_leaf_ratio`
(`helpers.py:611-628`) sums `title+text` over **every node (leaf and non-leaf)**,
so a severely over-nested tree inflates its denominator with spurious wrapper-node
titles, artificially deflating the ratio.

Separately, `_segment_label` (`converters.py:202-270`) doesn't recognize English
`"Article N"` headings (only German/digit/single-letter patterns), so Articles 3-6
get nested 4-5 levels deep under an unrelated sub-bullet — a "staircase" collapse.

**Decision (Part A).** Restrict `_tree_max_leaf_ratio`'s `total` accumulation to
leaf nodes only:

```python
def _tree_max_leaf_ratio(tree: dict) -> float:
    leaves = list(_walk_leaves(tree))
    if not leaves:
        return 0.0
    sizes = [len(n.get("title", "") + n.get("text", "")) for n in leaves]
    total = sum(sizes)
    if total == 0:
        return 0.0
    return max(sizes) / total
```

**Decision (Part B).** Add `Art(?:icle|\.)\s+\d+` and `§\s*\d+` alternatives to
`_segment_label` so English and §-prefixed headings receive an explicit depth:

```python
# In _segment_label, after the existing German patterns:
_ARTICLE_RE = re.compile(
    r"^(?:Art(?:icle|\.)\s+\d+|§\s*\d+)", re.IGNORECASE
)
# ... return depth 1 for matches
```

**Rationale.** (A) Non-leaf wrapper nodes are structural scaffolding; including
their titles in the denominator masks genuine concentration. (B) English
`Article N` is the most common heading pattern in the UAE legal corpus — omitting
it causes systematic mis-nesting.

### D4 — Marker leakage: widen hash-sentinel regex (`P1`, ~15 lines)

**Docs:** `aebf15b4`, `a6447d73`, `cbf7e6ad`, `d8e8a357`, `fb0554bf`

**Problem.** RFC-010 D5's `_INLINE_HASH_RE = re.compile(r"(?<=\S)#(?=\S)")` requires
non-whitespace on *both* sides. When a corrupted run's outer edges sit next to
whitespace (normal — في is a standalone word), the boundary `#`s survive unconverted
while interior `#`s convert — producing `#في#`/`#فيفي#`. This artifact breaks the
splitter's `_OVERSIZED_ORDINAL_RE` anchor and causes downstream articles to fuse
into the prior leaf.

**Decision.** Two changes:

1. Widen the regex to consume whole `#+` runs rather than per-character:
```python
_INLINE_HASH_RE = re.compile(r"#+")

def _fix_fi_hash_substitution(md: str) -> str:
    arabic_chars = sum(1 for c in md if "؀" <= c <= "ۿ")
    if len(md) > 0 and (arabic_chars / len(md)) > 0.15:
        # Replace hash runs that are NOT line-initial heading markers
        lines = md.splitlines(keepends=True)
        result = []
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("#") and " " in stripped.split("#")[-1][:5]:
                result.append(line)  # preserve heading markers
            else:
                result.append(re.sub(r"#+", "في", line))
        return "".join(result)
    return md
```

2. Move the fix **earlier** in the pipeline — before heading-depth inference — so
   في is restored as a single token before any heading regex sees the corrupted text.

**Rationale.** The per-char regex was the interim choice (RFC-010 D5). The boundary
`#`s that survive are the exact characters that poison the ordinal-match anchor. In
`aebf15b4`, this causes an entire second bundled legal instrument (Cabinet Resolution
1/2022) to be swallowed whole into one leaf.

### D5 — Giant-tail-blob: four additive sub-fixes (`P1`, ~60 lines total)

**Docs:** `6147c7d7`, `7dcf7cb7`, `8cfeca9a`/`bf7eb06f`, `b9cfac9c`, `acc20e08`

**Problem.** The RFC-005 Fix-1 splitter correctly finds the first N instances of a
repeating heading marker, then stops for the remainder. Four distinct sub-causes
identified:

| Sub-cause | Doc(s) | Root cause | Fix |
|-----------|--------|------------|-----|
| Size-gate bypass | `6147c7d7` | Residual leaf (19,959 chars) is under the 50,000-char `max_chars` gate at `helpers.py:1008` — ordinal matching never runs | Decouple size gate from marker-density: run ordinal matching on any leaf with detectable heading markers, regardless of char count |
| Missing `Schedule` pattern | `8cfeca9a`, `bf7eb06f` | `_OVERSIZED_ORDINAL_RE` has no `Schedule (N)` alternative | Add `Schedule\s+\(?(\d+)\)?` to the ordinal regex |
| Run-together headings | `7dcf7cb7` | Docling emits multiple `#######`-prefixed headings on one physical line; line-anchored regex never sees them | Add `_split_run_together_headings()` normalization pass before depth inference |
| Letter-suffix sub-clauses | `acc20e08` | `7.10.a`, `7.10.b` fail `_repromote_numbered_headings`'s digit-only trailing check (`converters.py:647-650`) | Extend promotion condition to accept a single trailing letter: `r"\d+[a-z]?"` |

**Decision.** All four fixes are additive and scoped:

```python
# D5a: helpers.py — lower the gate for marker-density check
# Before: if len(leaf_text) > max_chars:
# After:  if len(leaf_text) > max_chars or _has_heading_markers(leaf_text):

# D5b: helpers.py — extend ordinal regex
_OVERSIZED_ORDINAL_RE = re.compile(
    r"(?:§|Article|Section|مادة|Schedule)\s+\(?(\d+)\)?",
    re.IGNORECASE,
)

# D5c: converters.py — split run-together headings
def _split_run_together_headings(md: str) -> str:
    """Insert newlines before # heading markers that follow non-whitespace."""
    return re.sub(r"(?<=[^\n])(#{1,6}\s)", r"\n\1", md)

# D5d: converters.py — extend letter-suffix promotion
# converters.py:647-650 — change trailing \d+ to \d+[a-z]?
```

**Rationale.** Each sub-cause is independent and narrowly scoped. Together they
address the single largest defect class in the corpus (11+ of 25 docs).

### D6 — Chart/infographic text recovery via per-picture OCR (`P1`, ~40 lines)

**Docs:** `1f2a37f6`, `b644b8de`

**Problem.** Docling's layout model clusters chart data-labels and axis text into
the `Picture` cluster's bounding box; `export_to_markdown()` renders the whole
cluster as `<!-- image -->`, discarding co-located text. 764 clean characters exist
in the source (verified via PyMuPDF extraction); ~90 survive. The existing D1 OCR
escalation (RFC-010) only fires on a page-level image-line ratio >50%, so a chart
occupying part of a page never triggers it.

**Decision.** Per-picture OCR fallback — crop each `PictureItem`'s bbox, run the
existing Tesseract path against the crop, splice recovered text as a caption after
the `<!-- image -->` marker:

```python
async def _recover_picture_text(
    doc_path: str, pictures: list[dict], langs: str
) -> dict[int, str]:
    """OCR each picture bbox and return {picture_index: recovered_text}."""
    import fitz  # pymupdf — already a transitive dep via pymupdf4llm
    recovered = {}
    pdf = fitz.open(doc_path)
    for i, pic in enumerate(pictures):
        page = pdf[pic["page"]]
        clip = fitz.Rect(pic["bbox"])
        pix = page.get_pixmap(clip=clip, dpi=300)
        img_bytes = pix.tobytes("png")
        text = _tesseract_ocr(img_bytes, langs)
        if text and len(text.strip()) > 20:
            recovered[i] = text.strip()
    pdf.close()
    return recovered
```

Wire after `export_to_markdown()` returns: for each `<!-- image -->` marker with
a matching recovered text, append the text as `> [Chart text]: ...`.

**Rationale.** Region-scoped OCR fires regardless of page-level ratio. The 20-char
minimum avoids noise from OCR artifacts on decorative images. Uses the existing
Tesseract path — no new dependency.

**HR4 note.** This uses `fitz` (PyMuPDF/AGPL) for bbox cropping. The import is
scoped to this function and only fires when pictures are detected. This extends the
existing AGPL surface (already present via `pymupdf4llm`), not a new introduction.

### D7 — BiDi word-order normalization (`P1`, ~25 lines + dependency)

**Docs:** `6e8dc6f9`, `bbd28040`

**Problem.** Arabic words are correctly spelled but stored in visual/glyph order
rather than logical reading order — 97% of Arabic characters in `bbd28040` are in
presentation form. This is distinct from mojibake (no character substitution, pure
ordering). RFC-010 classified this as an upstream trade-off (Gap 5b); this audit
confirms it is fixable in our pipeline with `python-bidi` (pure-Python, MIT).

**Decision.** Add `reconstruct_bidi_order()` using `python-bidi`, applied per-line,
gated on the existing Arabic-ratio threshold:

```python
from bidi.algorithm import get_display

def reconstruct_bidi_order(text: str) -> str:
    """Reconstruct logical reading order from visual-order Arabic text."""
    arabic_chars = sum(1 for c in text if "؀" <= c <= "ۿ"
                       or "ﹰ" <= c <= "﻿")
    if len(text) == 0 or (arabic_chars / len(text)) < 0.15:
        return text
    lines = text.splitlines(keepends=True)
    return "".join(get_display(line) for line in lines)
```

Apply in `pdf_to_markdown_docling()` output, after `_fix_fi_hash_substitution`
and before heading-depth inference.

**Rationale.** `python-bidi` implements the Unicode BiDi Algorithm (UAX #9). The
per-line application preserves markdown structure. The Arabic-ratio gate ensures
German/English documents are untouched (zero false-positive risk).

**Dependency.** `python-bidi` — pure Python, MIT license, no C extension. Add to
`pyproject.toml` dependencies.

### D8 — Sparse mixed-script garble detection (`P1`, ~20 lines)

**Docs:** `92eebefa`, `c1ccd6e5`, `6147c7d7` (subset)

**Problem.** RFC-010 D3's garble heuristics check bulk ratios (PUA%, digit%,
repetition%) but miss **sparse, localized** substitution — a handful of corrupted
Latin fragments adjacent to Arabic script diluted across a full document never
crosses any threshold, so OCR escalation never fires.

**Decision.** Add a length-independent, per-node script-mixing check:

```python
_MIXED_SCRIPT_RE = re.compile(
    r"[؀-ۿ][\x20-\x7E]{1,8}[؀-ۿ]"  # Arabic-Latin-Arabic
    r"|[\x20-\x7E]{1,8}[؀-ۿ][\x20-\x7E]{1,8}"  # Latin-Arabic-Latin
)

def _has_sparse_mojibake(text: str, threshold: float = 0.02) -> bool:
    """Detect localized Latin/digit fragments glued to Arabic script."""
    if len(text) < 100:
        return False
    matches = _MIXED_SCRIPT_RE.findall(text)
    return (len(matches) / max(len(text.split()), 1)) > threshold
```

Wire into `_tree_is_garbled` as an additional check, and into
`_flat_text_is_garbled`. When triggered, reactivates the existing OCR-escalation
path (same as RFC-010 D3 wiring).

**Rationale.** The per-node granularity catches corruption that bulk-ratio checks
dilute away. The 2% threshold is calibrated against `92eebefa` (21.4% mixed-script
ratio) while avoiding false positives on `b1a72fb2` (which has legitimate
transliterated names).

### D9 — Table rowspan forward-fill (`P1`, ~15 lines)

**Doc:** `e544d939` (GHV-TKV-Tarif)

**Problem.** The Katze table's merged `Selbstbehalt` label isn't forward-filled into
22 data rows, while the structurally identical Hund table on the same page correctly
forward-fills. The pipeline's own `suspected_miss=true` / elevated `empty_cell_ratio`
already flags this. Root cause: `_flat_parse_table` (`helpers.py:771-786`) only
forward-fills when the *entire* row is empty, not when only column 0 is empty.

**Decision.** Add leading-column forward-fill, scoped to column 0 only:

```python
def _forward_fill_leading_column(rows: list[list[str]]) -> list[list[str]]:
    """Forward-fill empty cells in column 0 (merged rowspan headers)."""
    last_val = ""
    for row in rows:
        if row and row[0].strip():
            last_val = row[0].strip()
        elif row and not row[0].strip() and last_val:
            row[0] = last_val
    return rows
```

**Rationale.** Modeled on the working Hund reference table. Scoped to column 0 only
to avoid corrupting data columns. The pipeline already self-flags this defect via
`suspected_miss` — this fix eliminates the flag at source.

### D10 — Preamble node synthesis (`P1`, ~15 lines)

**Doc:** `722eb392` (GHV Reitlehrer Haftpflicht policy)

**Problem.** Section 1 (the policy's defining "who is covered" clause) is missing
entirely. Root cause: `extract_nodes_from_markdown` (`page_index_md.py:32-57`)
silently discards any markdown text before the first detected `#` heading — no
warning, no synthetic node.

**Decision.** Synthesize a preamble node for content preceding the first heading:

```python
# In extract_nodes_from_markdown, before the heading-split loop:
first_heading_idx = _find_first_heading(md_lines)
if first_heading_idx > 0:
    preamble_text = "\n".join(md_lines[:first_heading_idx]).strip()
    if len(preamble_text) > 50:  # non-trivial preamble
        nodes.insert(0, {
            "title": "[Preamble]",
            "text": preamble_text,
            "depth": 0,
            "line_num": 0,
        })
```

**Rationale.** Many legal and insurance documents have substantive content before
the first heading (definitions, scope, effective date). Dropping it silently violates
the user's expectation that the full document is indexed. The 50-char threshold
avoids synthesizing nodes for trivial whitespace or blank lines.

## Implementation Plan

### Batch 1 — P0 fixes (verdict engine + tooling, no extraction changes)

| Step | ID | Change | Files |
|------|----|--------|-------|
| 1.1 | D1 | Import `_SUPPORTED` from `client.py` | `preprocess_client.py` |
| 1.2 | D2 | Add `_tree_is_reordered()` + wire into validate/classify | `helpers.py` |
| 1.3 | D3A | Fix `_tree_max_leaf_ratio` denominator (leaf-only) | `helpers.py` |
| 1.4 | D3B | Add English `Article N` / `§ N` to `_segment_label` | `converters.py` |

### Batch 2 — P1 marker & splitter fixes (independent of Batch 1)

| Step | ID | Change | Files |
|------|----|--------|-------|
| 2.1 | D4 | Widen hash regex to `#+`, reorder pipeline | `converters.py` |
| 2.2 | D5a | Decouple size gate from marker-density | `helpers.py` |
| 2.3 | D5b | Add `Schedule` to ordinal regex | `helpers.py` |
| 2.4 | D5c | Add `_split_run_together_headings()` | `converters.py` |
| 2.5 | D5d | Extend letter-suffix promotion | `converters.py` |

### Batch 3 — P1 extraction quality (partially depends on Batch 2 for reprocessing)

| Step | ID | Change | Files |
|------|----|--------|-------|
| 3.1 | D6 | Per-picture OCR fallback for chart text | `converters.py` |
| 3.2 | D7 | BiDi normalization pass + `python-bidi` dep | `converters.py`, `pyproject.toml` |
| 3.3 | D8 | Sparse mixed-script garble detection | `helpers.py` |
| 3.4 | D9 | Leading-column forward-fill | `helpers.py` |
| 3.5 | D10 | Preamble node synthesis | `page_index_md.py` |

### Batch 4 — Revalidation

| Step | Change | Files |
|------|--------|-------|
| 4.1 | Full 26-file corpus reprocess via `preprocess_client.py` | — |
| 4.2 | Regenerate verdict table; verify 2 wrong-PASS verdicts corrected | — |
| 4.3 | Expected outcome: wrong-PASS count 2→0; MARGINAL count 12→≤6 | — |

## Test Strategy

### D1 — Batch SUPPORTED set

- Unit test: assert `preprocess_client.SUPPORTED` includes `.jpg`, `.xlsx`, `.png`.
- Integration: run `preprocess_client.py` against a `.jpg` — assert job is enqueued.

### D2 — Content-ordering check

- Unit test: tree with monotonic `start_index` → `_tree_is_reordered` returns False.
- Unit test: tree with regressing `start_index` → returns True.
- Unit test: `classify_verdict` on a reordered tree → verdict < PASS, reason
  contains `"reordered"`.
- Regression: assert `54e92c0a` no longer receives PASS after reprocessing.

### D3 — Ratio denominator + heading labels

- Unit test: tree with deep non-leaf wrappers → `_tree_max_leaf_ratio` uses
  leaf-only denominator (higher ratio than before).
- Unit test: `_segment_label("Article 5")` → returns explicit depth (not None).
- Unit test: `_segment_label("§ 12")` → returns explicit depth.
- Regression: assert `a4c1b522` no longer receives PASS after reprocessing.
- Regression: German heading labels (`Abschnitt`, `Teil`) still recognized.

### D4 — Marker leakage regex

- Unit test: `"text #في# more text"` → `"text في more text"` (boundary `#` consumed).
- Unit test: `"#فيفيفي#"` → `"في"` (whole run collapsed to single في).
- Unit test: `"## Heading"` → preserved (heading markers untouched).
- Unit test: non-Arabic text with `#` → unchanged.

### D5 — Tail-blob sub-fixes

- D5a: unit test — leaf with heading markers but <50k chars → ordinal matching runs.
- D5b: unit test — `"Schedule (3)"` matches `_OVERSIZED_ORDINAL_RE`.
- D5c: unit test — `"text### Heading"` → `"text\n### Heading"`.
- D5d: unit test — `"7.10.a"` accepted by promotion condition.

### D6 — Per-picture OCR

- Unit test: mock a PDF with a picture bbox containing text → OCR fires, text
  recovered and spliced after `<!-- image -->`.
- Unit test: picture bbox with <20 chars recovered → no caption added.
- Integration: `1f2a37f6` reprocessed → chart text present in output.

### D7 — BiDi normalization

- Unit test: visual-order Arabic text → logical order after `reconstruct_bidi_order`.
- Unit test: German/English text → unchanged (Arabic-ratio gate).
- Unit test: mixed Arabic/English paragraph → Arabic reordered, English preserved.

### D8 — Sparse mixed-script detection

- Unit test: Arabic text with glued Latin fragments → `_has_sparse_mojibake` True.
- Unit test: normal Arabic text with transliterated names → False.
- Unit test: German text → False.
- Integration: `92eebefa` triggers garble → OCR escalation.

### D9 — Table forward-fill

- Unit test: table rows with empty column 0 → forward-filled from prior row.
- Unit test: table rows with non-empty column 0 → unchanged.
- Unit test: data columns (1+) with empty cells → NOT forward-filled.

### D10 — Preamble node

- Unit test: markdown with content before first heading → preamble node created.
- Unit test: markdown starting with heading → no preamble node.
- Unit test: trivial whitespace before heading (<50 chars) → no preamble node.

## Risks

1. **D2 ordering check may reject documents with intentional appendices.** Some
   legal documents have annexes or schedules that reference earlier articles out of
   order. Mitigation: the check uses `start_index` (source-document position), not
   logical reference order — an annex that physically follows the main body won't
   trigger. If false positives appear, the check can be softened to only flag
   regressions >N lines.

2. **D3A ratio denominator change shifts all existing verdicts.** Every document's
   `max_leaf_ratio` will increase when computed leaf-only. Mitigation: the MARGINAL
   threshold (0.25) is already calibrated against leaf content; the change makes the
   metric match its name. A full corpus reprocess (Batch 4) will update all stored
   verdicts.

3. **D4 widened regex may over-consume `#` in edge cases.** The heading-marker
   preservation logic must be robust. Mitigation: explicit line-by-line processing
   with heading-marker detection; unit tests for both Arabic and non-Arabic text.

4. **D6 per-picture OCR adds processing time.** Each picture bbox requires a
   PyMuPDF crop + Tesseract call. Mitigation: only fires when pictures are detected
   in the Docling output; most text-only documents are unaffected. The existing
   `_OCR_ESCALATION` kill-switch applies.

5. **D7 `python-bidi` is a new runtime dependency.** Mitigation: pure Python, MIT,
   no C extension, well-maintained (Unicode BiDi Algorithm implementation). Gated on
   Arabic-ratio threshold — zero overhead for non-Arabic documents.

6. **D8 sparse mojibake regex may flag legitimate mixed-script text.** Arabic
   documents with frequent English technical terms could trigger false positives.
   Mitigation: the threshold (2% of words) is calibrated against real corpus
   documents with legitimate mixed-script content; the check requires the
   Arabic-Latin-Arabic pattern specifically, not just co-occurrence.

## Affected Documents (per decision)

| Decision | Doc IDs |
|----------|---------|
| D1 | `UNSUPPORTED` (`.jpg`) |
| D2 | `54e92c0a` |
| D3 | `a4c1b522` |
| D4 | `aebf15b4`, `a6447d73`, `cbf7e6ad`, `d8e8a357`, `fb0554bf` |
| D5 | `6147c7d7`, `7dcf7cb7`, `8cfeca9a`, `bf7eb06f`, `b9cfac9c`, `acc20e08` |
| D6 | `1f2a37f6`, `b644b8de` |
| D7 | `6e8dc6f9`, `bbd28040` |
| D8 | `92eebefa`, `c1ccd6e5`, `6147c7d7` (subset) |
| D9 | `e544d939` |
| D10 | `722eb392` |

## References

- **Source report:** `DOC_STORE_CORPUS_REPORT.md` (this RFC's input)
- **Prior RFCs:** RFC-005 (splitter redesign), RFC-010 (corpus gap remediation —
  garble gate, في→# interim, image-ratio OCR, heading indent normalization)
- **Key source files:**
  - `src/pageindex_mcp/helpers.py` — validate_tree, classify_verdict, garble gate,
    splitter, _tree_max_leaf_ratio, _flat_parse_table
  - `src/pageindex_mcp/converters.py` — pdf_to_markdown_docling, _segment_label,
    _fix_fi_hash_substitution, _repromote_numbered_headings
  - `src/pageindex_mcp/page_index_md.py` — extract_nodes_from_markdown, build_tree
  - `src/pageindex_mcp/client.py` — index orchestration, _SUPPORTED, OCR escalation
  - `preprocess_client.py` — batch preprocessing, SUPPORTED set
- **Memory entries:** `fix1-redesign-and-tessdata-prebake`,
  `corpus-gap-analysis-2026-07-14`, `fabricated-corpus-report-2026-07-17`,
  `corpus-audit-phase2-2026-07-17`
