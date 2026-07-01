---
id: RFC-005
title: Five Fixes for Hard-Corpus Ingestion (Arabic / English / German)
status: implemented (uncommitted on feat/test-arabic-documents)
date: 2026-07-01
plan-impact: yes
supersedes-decisions-in: []
---

## Context

The Phase E performance run (`issue/data2_performance_report.md`, 35 files, 70 agents,
~1.7M tokens) asked whether ingestion **degrades** when pushed past the German-T&C
validation set onto UAE/Arabic law, scanned images, statistical tables, and mixed
binaries. Verdict: **INTACT 9 / DEGRADED 21 / FAILED 5**. The system did not collapse —
every clean text-layer prose doc ingested — but it degraded along two axes the German
set never stressed (**deep hierarchy**, **tabular content**) and hard-failed on **2
unsupported formats** + **3 scanned/corrupt-layer Arabic docs**.

This RFC formalizes the report's 5 recommended fixes. Every fix is validated against
**Arabic, English, and German**, because the corpus that exposed these defects is
tri-lingual and the dominant failure (tail-blob hierarchy collapse) is corpus-agnostic
(it hits English laws too).

### Hard-Rule envelope (CLAUDE.md — binding)

- **HR1** no fix is framed as beating vector RAG on accuracy — structure/retrieval only.
- **HR2** no fix adds a new persisted artifact type; xlsx/image/escalated docs land only
  in existing `uploads/`, `processed/*.json`, `processed/*.flat.json`, `*.meta.json` —
  all already cascaded by `delete_doc`.
- **HR3** image/VLM stays disabled by default; all OCR is local Tesseract (no LLM
  egress).
- **HR4** only new dependency is **openpyxl (MIT)**; no PyMuPDF/pymupdf4llm/AGPL added.
  Vendored `page_index_md.py` is not edited.
- **HR5** every path that could persist (Fix-1 safety net, Fix-3 retry) re-runs
  `validate_tree()` before save; a still-garbled tree is rejected, never stored.

## What shipped

All five fixes are implemented in the working tree on `feat/test-arabic-documents`
(diverged from `master` at `7d5f343`). None of this has been committed yet — it lives as
uncommitted changes to `client.py` (+94), `converters.py` (+241), `helpers.py` (+417),
`metrics.py` (+6), plus test additions across `test_depth_inference.py`,
`test_converters_contract.py`, `test_helpers_contract.py`, `test_storage_contract.py`.

### Fix 1 — Node-tail-blob hierarchy splitter (`helpers.py`)

**Root cause.** Arabic legal headings (`المادة`, `الباب`, etc.) never received a depth
because `_segment_label`'s Latin-only gates rejected them, so `numbering_depth` stayed
flat. With no depth, the vendored `extract_node_text_content` slicer let the last
surviving heading swallow the document tail (e.g. Penal Code Art. 9 = 236,413 chars).

**Implemented approach — a bounded post-split safety net, not a depth-recovery
rewrite.** `split_oversized_leaf_nodes()` (`helpers.py`) recurses the tree after
`_run_md_to_tree` and before `validate_tree`, and any oversized leaf is split on
in-line ordinal markers:

- `_fold_with_index_map` — NFKC-folds text while keeping an index map back to the
  original string (handles Arabic presentation forms, e.g. `ﺍﳌـﺎﺩﺓ`, without corrupting
  offsets).
- `_ordinal_value` / `_longest_increasing_run` — extracts ordinal numbers from matched
  markers and keeps only a strictly increasing cross-reference run, so an in-body mention
  of "Article 5" inside Article 9's text doesn't get treated as a real split point.
- `_looks_like_frontmatter_toc` — a guard so a table-of-contents block (which also
  contains a run of increasing ordinals) isn't mistaken for real body text and shredded.
- `_split_on_paragraph_markers` / `_apply_split` — do the actual line-preserving split
  into sibling nodes.

This is **inline-match-based**, not line-anchored like the original design intent —
that redesign was necessary because real gazette markers (e.g. `457/458`) appear mid-line,
not just at line starts.

**Wired in** `client.py::index()`: `result["structure"] = split_oversized_leaf_nodes(...)`
runs immediately before `validate_tree`, and again after the Fix-3 OCR-escalation retry.

### Fix 2 — Table fidelity (`helpers.py`)

- **2a Column-stitching** — `stitch_continuation_tables(blocks)` merges adjacent
  `role: 'table'` flat blocks when a later block is a pagination continuation of an
  earlier one (same row count, numeric/date headers, no row-label column), re-keying on
  the anchor's label column and regenerating `row_records`.
- **2b RTL ordering** — `table_is_rtl(block)` classifies a table as right-to-left via
  Arabic-script character ratio, consumed by the stitcher so column concatenation and
  intra-row ordering read correctly for Arabic tables.
- **2c Empty-cell / miss detection** — `flag_empty_cells(block)` annotates a
  non-mutating `quality: {empty_cell_ratio, suspected_miss}` signal for downstream use.

Wired into `route_and_extract_flat()`: `blocks = stitch_continuation_tables(blocks)`
followed by a `flag_empty_cells` pass per block.

**Thin-evidence flag (carried from the plan):** 2b/2c ship behind synthetic RTL
fixtures — no reproduced real-world Arabic paginated table exists in the corpus. Do not
claim production RTL-table coverage without a real Arabic multi-page table fixture.

### Fix 3 — `force_full_page_ocr` escalation on garble (`client.py`, `converters.py`)

On `reason == "garbling"` for a `.pdf`, `index()` retries the Docling conversion once
with `force_full_page_ocr=True` and a Fix-5-detected language set, re-runs the Fix-1
splitter, and re-validates. Escalation is gated by `_OCR_ESCALATION`
(`OCR_ESCALATION` env var, default on) and only fires once per doc.

Language selection for the retry deliberately does **not** trust the garbled text layer:
it detects from the **filename** first, then unions in whatever `detect_ocr_langs`
extracts from the (garbled) markdown, so a corrupt-CMap doc that decodes to Latin
mojibake doesn't silently OCR in English only.

`metrics.py` gained `OCR_ESCALATION_TOTAL{result}` (`recovered` | `still_garbled` |
`error`). **HR5 held:** the retry re-validates and still terminally rejects a tree that
stays garbled after escalation — it never bypasses the gate.

### Fix 4 — Format adapters: `.xlsx` and image input (`client.py`, `converters.py`)

- **`.xlsx`** — `xlsx_to_markdown()` (openpyxl, MIT) converts each sheet to a markdown
  table, feeding the existing flat-table path (spreadsheets are inherently flat and
  route via `depth < 2` → flat success, reusing Fix-2a stitching for free).
- **Image input** (`.png/.jpg/.jpeg/.tiff/.tif`) — `image_to_markdown()` routes through
  local Tesseract OCR only, using a superset language list (`ara, deu, eng`) since there
  is no pre-existing text layer to sample for language detection. **VLM stays off** —
  no code path in this branch enables VLM; this is consistent with RFC-004's
  `VLM_MODE=disabled` default, not a re-decision of it.

`_SUPPORTED` extended to include `.xlsx` and `_IMAGE_EXTS`.

### Fix 5 — Auto language detection + on-demand tessdata (`converters.py`)

- `detect_ocr_langs(sample)` — deterministic, no model: Unicode-block ratio
  classification (Arabic range ⇒ `ara`; German diacritics/ß ⇒ add `deu`; else `eng`).
  No network, no LLM call.
- `ensure_tessdata(langs)` — checks `TESSDATA_PREFIX` for `<lang>.traineddata`;
  `_try_download_tessdata` fetches on demand if `TESSDATA_ALLOW_DOWNLOAD` permits network
  egress; falls back to `deu,eng` and logs rather than raising if a language can't be
  provisioned. Consumed by both Fix-3 (PDF OCR escalation) and Fix-4 (image OCR).

Companion: `scripts/prebake_tessdata.sh` (untracked, not part of this RFC's committed
scope) pre-bakes tessdata into a container image for egress-limited deploys, mirroring
the existing `DOCLING_ARTIFACTS_PATH` precedent.

## Contracts

Governance contracts (`.agents/contracts/`) initially lagged this implementation: the
new tests only cited pre-existing IDs (`CONV-01`, `FLAT-01/02/03`, `WORKER-01-C2`), and
`conv-01.yaml`'s format-list text still enumerated only the pre-Fix-4 extensions. Closed
2026-07-01:

- **`split-01.yaml`** (`SPLIT-01-C1..C3`) — Fix 1 tail-blob splitting, front-matter/TOC
  guard, idempotency. Tagged onto existing `test_depth_inference.py` tests.
- **`table-01.yaml`** (`TABLE-01-C1..C3`) — Fix 2 stitching, RTL ordering, empty-cell
  flag. Tagged onto existing `test_helpers_contract.py` tests.
- **`ocr-01.yaml`** (`OCR-01-C1..C3`) — Fix 3 escalation retry, filename-first language
  selection, terminal-reject-on-still-garbled / retry-exception. **No test existed for
  this behavior before this pass** — added 4 new no-infra tests to
  `test_client_contract.py` mocking `pdf_markdown_converters`/`validate_tree`/
  `pdf_to_markdown_docling`/`detect_ocr_langs`/`ensure_tessdata`.
- **`lang-01.yaml`** (`LANG-01-C1..C3`) — Fix 5 detection + tessdata provisioning.
  C1/C2 tagged onto existing tests; C3 (pre-baked no-op path) had no test — added one.
- **`conv-01.yaml`** — `CONV-01-C3` boundary text updated to include `.xlsx`/image
  extensions; new `CONV-01-C4`/`CONV-01-C5` added for the `.xlsx` and image dispatch
  branches in `index()` specifically (previously only the underlying converter
  functions were unit-tested, not the `index()`-level dispatch) — added 2 new tests.

`scripts/gates/contracts.sh` now passes clean, including all newly added IDs.

## Verification

Full suite as of this RFC: **251 passed, 6 skipped** (`uv run pytest`), including the
Fix-1 splitter tests (fold/index-map, ordinal-run guard, front-matter/TOC guard,
paragraph-marker split) in `test_depth_inference.py`, Fix-2 stitch/RTL/empty-cell tests
in `test_helpers_contract.py`, Fix-3 OCR-escalation dispatch tests and Fix-4 `.xlsx`
/image dispatch tests in `test_client_contract.py`, and Fix-5 language-detection tests
in `test_converters_contract.py`.

**Real end-to-end verification (this session, 2026-07-01):** ran the actual ingestion
CLI (`python -m pageindex_mcp.converters_cli`, not a standalone bypass script) against
the real Human Rights Arabic PDF, through `configure_litellm → CustomPageIndexClient.index()
→ litellm → Azure gpt-4.1 → validate_tree → save_doc`, persisting to MinIO
(`processed/eaca6add.json`). Result: **59 leaf nodes, max leaf text 35,190 chars** —
down from a pre-fix baseline of **26 leaves / 319,975-char max leaf**. This matches an
earlier standalone-script verification exactly, confirming the fix behaves identically
through the real LLM call and MinIO persistence path, not just in isolation.

## Honesty notes (carried forward, not resolved by this RFC)

- Fix-2b/2c Arabic RTL table handling ships behind **synthetic** fixtures only — no
  field-reproduced Arabic paginated table exists in the corpus yet.
- Fix-3's German/English OCR-escalation recovery has lower evidence than the Arabic
  core case (the corrupt-CMap مرسوم docs are the reproduced target; de/en escalation is
  asserted via forced-garble fixtures, not a real corrupt de/en corpus doc).
- Fix-1's splitter is inline-match-based per-doc heuristic tuning (ordinal-run,
  front-matter guard) — it is a bounded safety net, not a guarantee that every possible
  gazette numbering convention is covered.

## Status / next steps

Not committed. All work sits as uncommitted changes on `feat/test-arabic-documents`.
No commit has been requested by the user; per repo git-safety protocol this RFC records
the implementation as-is without assuming a commit will follow.
