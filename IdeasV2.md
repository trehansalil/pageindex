# PageIndex PDF Ingestion: Root-Cause Analysis & Remediation Plan for the GHV Insurance Corpus

*Prepared for the repo maintainer — `trehansalil/pageindex` (FastMCP + arq + MinIO + Redis), with the user-owned `trehansalil/PageIndex-salil` library fork.*

---

## 1. Executive Summary

- **The four `issue/data/*.pdf.pdf` documents fail because of the PDF *text extractor*, not OCR, not the TOC, not the `.pdf.pdf` filename.** PageIndex's `.pdf` path is hardwired to PyPDF2, which garbles these born-digital, *tagged* German insurance PDFs into spurious intra-word spaces, unsplit ligatures, and column-bleed (e.g. `Geme innützig e Ha ftpﬂ icht-Versicherungsanstal t` instead of `Gemeinnützige Haftpflicht-Versicherungsanstalt`).
- **The clean extractor exists in the library but is unreachable.** `get_page_tokens` has a working PyMuPDF branch (`utils.py:397-409`), but `page_index_main` calls it with no `pdf_parser` arg (`page_index.py:1077`), the public `page_index()` signature has no such parameter (`page_index.py:1113`), and `config.yaml` has no `pdf_parser` key — so PyMuPDF can never be selected without editing the fork.
- **Garbled text then breaks the *second* stage: the LLM-based TOC detection.** `tree_parser` → `check_toc` → `find_toc_pages`/`toc_extractor`/`verify_toc` (`page_index.py:696, 341, 222, 900`) reasons over corrupt text; `verify_toc` collapses, `process_no_toc` fabricates a TOC, and `list_to_tree` (`page_index.py:440`) degrades to a flat/empty tree. So even a "successful" run yields a near-useless hierarchy.
- **The robust path the repo already owns sidesteps both failures.** `.md/.markdown/.txt` route to `_run_md_to_tree` → `md_to_tree`, whose skeleton (`extract_nodes_from_markdown` → `build_tree_from_nodes`, `page_index_md.py:36, 185`) is **pure-Python regex over `#` headers, no API key, no TOC detection.** The `.docx/.html` branches in `client.index()` already use the "convert → temp `.md` → `_run_md_to_tree`" pattern — PDFs just need the same treatment.
- **Empirically confirmed offline (no API key):** PyPDF2 → **0** `#` headers → **0** tree nodes for both AKB and Komfort. `pymupdf4llm.to_markdown()` → clean text + **388 / 209** clean-titled nodes; with a tiny numbering-to-level post-pass, a real depth-3 tree (`A` → `A.1` → `A.1.1`).
- **Recommended extractor: `pymupdf4llm`** — the only tool that simultaneously fixes garbling, resolves ligatures, reconstructs the IPID and SF-Klasse tables as markdown tables, and emits headings carrying the document's own clause numbering. Its one defect (all headings flattened to `##`) is repaired by a ~30-line pure-Python re-leveling pass driven by the numbering already in each heading and/or the embedded bookmark TOC.
- **License caveat:** `pymupdf`/`pymupdf4llm` are AGPL-3.0 (or paid Artifex). This obligation **already exists** — `pymupdf>=1.27.2.2` is a direct dependency today. `docling` (Tier 1) is MIT and avoids the issue at the cost of a PyTorch/model-download footprint.
- **The pipeline has no quality gate.** `client.index()` persists an empty/1-node tree silently. The single highest-leverage *operational* fix is a pure-Python `validate_tree()` assertion before `save_doc`, surfaced via the existing arq job-status + Prometheus machinery.
- **Strategic opportunity:** the three AVB-PHV files are *tiers of one product* sharing a clause-code namespace (`-B`/`-K`/`-P` suffixes on a common stem like `A1-6.14-01`). A thin `networkx` cross-doc graph over the per-doc trees answers "what does Komfort add over Basis?" — which no per-doc tree can. **Do not** adopt full Microsoft GraphRAG for a 4–7 doc corpus.

---

## 2. Why the `issue/data` PDFs Are Not Processed Properly — Full Root-Cause Chain

The failure is a *chain*, not a single bug. Each link compounds the next.

### RC1 (critical) — PyPDF2 is hardcoded and garbles these PDFs
`page_index()` (`page_index.py:1113`) → `page_index_main()` (`page_index.py:1066`) → `get_page_tokens(doc, model=opt.model)` at **`page_index.py:1077`**. The call passes **no** `pdf_parser` argument, so `get_page_tokens` (`utils.py:387`, signature `pdf_parser="PyPDF2"`) defaults to PyPDF2. `PyPDF2.extract_text()` on these tagged InDesign PDFs produces:
- spurious intra-word spaces — `Geme innützig e Ha ftpﬂ icht-Versicherungsanstal t`,
- unsplit ligatures — `ﬂ`, `ﬁ` (`Haftpﬂ icht`),
- multi-column bleed — `GHV VE RSICHERUNG GHV VE RSICHERUNG 06151 3603-0` (two columns merged onto one line),
- page-header bleed into body — `...Seite 4Welche Verpflichtungen habe ich?`.

> **Correction to prior context:** the garbling is *real but localized* to the cover/front-matter pages. PyPDF2 inflates the cover short-fragment count to ~22–23 vs ~13 for clean extractors. The high "short-word" fractions on AKB p38–43 are **not** garbling — they are genuine dense SF-Klasse rating tables, which PyPDF2 *and* plain PyMuPDF both destroy via column linearization.

### RC2 (critical) — the clean PyMuPDF branch is unreachable via the public API
`utils.py:397-409` contains a working PyMuPDF branch (`import pymupdf` at `utils.py:11`, handles file paths and `BytesIO`) that extracts these PDFs cleanly. It cannot be selected:
- `page_index()` / `page_index_main()` accept no `pdf_parser` (`page_index.py:1113-1121, 1066`),
- `config.yaml` has no `pdf_parser` key, and `ConfigLoader._validate_keys` (`utils.py:665-668`) *rejects* unknown keys — so the knob must be declared in `config.yaml` first,
- `page_index_main` never reads `opt.pdf_parser`.

> **Correction:** the blocking call is `page_index.py:1077`, **not** `utils.py:1077`; the `pymupdf` import is present, so this is purely an *API-plumbing* gap, not a missing dependency.

### RC3 (high) — garbled text breaks the LLM TOC-detection stage, collapsing the tree
`tree_parser` (`page_index.py:1029`) → `check_toc` (`page_index.py:696`) → `find_toc_pages` (`341`) → `toc_detector_single_page` (`104`, one LLM call/page over pages 0..`toc_check_page_num`) → `toc_extractor` (`222`) → `detect_page_index` (`202`). Then:
- branch at `page_index.py:1033`: `page_index_given_in_toc == "yes"` → `meta_processor` (`959`) → `process_toc_with_page_numbers`; else → `process_no_toc`.
- `verify_toc` (`900`, via `check_title_appearance`) cascades: accuracy `1.0` accept; `>0.6` → `fix_incorrect_toc` (LLM ×3); else recurse to weaker modes, terminating in `raise Exception("Processing failed")` near `page_index.py:996`. It also early-returns `0` if the last index `< pages/2` (`911-913`).

On garbled text `verify_toc` collapses → `process_no_toc` fabricates a TOC over corrupt text → `list_to_tree` (`440-468`) gets bad structure codes → returns `[]` → `post_processing` degrades to a **flat list**.

> **Correction:** `tree_parser` selects only `…with_page_numbers` or `…no_toc`; `…no_page_numbers` is only a `meta_processor` fallback (`991-996`).

### RC4 (high) — the repo routes `.pdf` into exactly this fragile path
`client.index()` (`src/pageindex_mcp/client.py:94`) routes `.pdf` → `_run_page_index` (the PyPDF2 + LLM-TOC path, `client.py:248-257`). The robust `_run_md_to_tree` (`client.py:259-280`) serves only `.md/.markdown/.txt` plus the `.docx/.pptx/.html` markdown fallbacks. **PDFs never reach the pure-Python header tree builder** — even though that builder is exactly what these well-numbered documents need.

### RC5 (medium) — `issue/data` is never discovered automatically
No automated path picks up `issue/data`. `preprocess_client.py` scans only `doc_store/` (`preprocess_client.py:101, 117`), which is empty/absent on this machine. To ingest: copy the four files into `doc_store/` and run `preprocess_client.py`, or `POST /upload/files`.

### RC6 (medium) — `upload.py` is a dead tool; `CLAUDE.md` is stale
`upload.py:30` invokes an MCP tool `process_document` that is **not registered** — `server.py:24-28` registers only the 5 query tools; `tools/processing.py` is a stub. `CLAUDE.md` claiming `process_document` / `upload_and_process_document` tools exist is stale.

### RC7 (low, cosmetic) — `.pdf.pdf` is harmless
`Path(...).suffix == ".pdf"`, so routing is correct. The full filename `*.pdf.pdf` is used consistently as the SHA-256 dedup key, MinIO object name, staging key, and `doc_name` (`client.py:65-66`, `storage.py:193, 264`). No failure — purely cosmetic.

**Net:** even if you fixed *only* RC1/RC2 (force PyMuPDF), you'd still hit RC3 on any doc lacking a classic page-numbered TOC. The durable fix bypasses RC1+RC3 together by going **PDF → structured markdown → `md_to_tree`**.

---

## 3. What the Data Actually Is

### 3.1 Provenance and structure
All four are **born-digital, TAGGED A4 PDFs** from GHV Versicherung (Adobe InDesign + Adobe PDF Library) with a **real text layer** and — critically — an **embedded bookmark/outline TOC** the current pipeline ignores entirely.

> **Corrections to prior context:** only **AKB** uses CenturyGothic; the three **AVB-PHV** files use **Montserrat** (InDesign 18.1 / PDF Library 17.0, not 21.3/18.0). All four carry an embedded outline with page numbers and the doc's own clause numbering in the titles: **AKB 23, Basis 134, Komfort 155, Premium 169** entries.

Each document has a consistent **three-block layout**:

1. **Cover (p1)** — issuer block + an "Allgemeine Versicherungsbedingungen im Überblick" mini-TOC.
2. **IPID** — "Informationsblatt zu Versicherungsprodukten", the EU-standardised 2-page product fact sheet (AKB p2–3 / AVB p3–4). Genuine **two-column** layout (left col `x≈34–280` "Was ist versichert?" beside right col `x≈349–562` "Was ist nicht versichert?" at the same `y`), with green/grey Q&A coverage boxes. **Non-binding** — must not be cited as the legal answer.
3. **Body (binding conditions)** with a deterministic numbering spine.

**AKB numbering (motor):** lettered Parts `A`…`L`/`Q` (`A` = "Welche Leistungen umfasst Ihre Kfz-Versicherung?") → dotted `A.1` (Kfz-Haftpflicht), `A.2` (Kasko), `A.3` (Fahrerschutz) → `A.1.1`/`A.1.2`… sub-clauses → lettered `a/b/c` leaf items. Appendices: **Anhang 1** "Tabellen zum Schadenfreiheitsrabatt-System" (p38, real tables), Anhang 2 (Berufsgruppen, p44), Anhang 3 (Fahrzeuge, p45), plus Kfz-USV (p46).

**AVB-PHV numbering (private liability):** `Teil A` (Versicherungsumfang) / `Teil B` (allgemeiner Teil) → `Abschnitt A1/A2/A3`, `B1`–`B4` → dashed `A1-1`, `A1-6.3.1`, `B4-3.2` → trailing **"Besondere Bedingungen und Klauseln zu Teil A und Teil B"** whose clauses use a strict ID grammar `A<sec>-<subsec>[.<dots>]-<seq>-<TIER>`, e.g. `A1-1-01-P`, `A1-6.3.1-02-P`. Each Besondere-Bedingung clause opens with a cross-reference (`"Mitversichert ist abweichend von A1-1 …"`) — a ready-made *amends* edge back to its Teil-A base clause.

> **Key structural insight:** the real hierarchy lives in (1) the embedded bookmark TOC and (2) the alphanumeric numbering embedded in every heading — **NOT in font size.** Headings are visually distinguished only by a bold flag + a tab between code and title (`A.1.1\t\tWas ist versichert?`), and the true clause headings sit at the **same ~8.9 pt** body size. This is exactly why every font-size-based heading detector (`pymupdf4llm`, Docling) flattens these docs.

### 3.2 Extractor bake-off (empirical, on the actual four PDFs)

| Tool | Text quality | Reading order (2-col IPID) | Tables | Heading hierarchy | MD-ready | Verdict |
|---|---|---|---|---|---|---|
| **PyPDF2** (`utils.py:387` default) | **Broken** — intra-word spaces, unsplit ﬂ/ﬁ, column bleed on cover | OK only because IPID blocks are well-separated | **Destroyed** (SF-Klasse → number stream) | **None** — no `#`, tabs collapsed | No | **Do not use** |
| **PyMuPDF `get_text()` / `blocks` (y,x)** | **Good** — clean, no intra-word spaces | Correct | Destroyed as text (but `dict`/`find_tables` expose coords) | No `#`; **but `get_toc()` = 23/134/155/169 outline entries** + per-span size/bold | Needs synthesis | **Fallback / structural-seed source** |
| **`pdftotext -layout`** (poppler) | **Good** — also resolves ﬂ ligature | Correct | **Preserved visually** (space-aligned, not cells) | None | No | Strong fidelity runner-up; external subprocess, not an MD emitter |
| **`pymupdf4llm.to_markdown()`** | **Excellent** — clean, ligatures resolved | Correct | **Preserved as structured `\| … \|` markdown tables — only tool that does** (IPID + SF-Klasse verified row-by-row) | `##` headings carrying the doc's own numbering (`A.1`, `B4-3.2`, `A1-1-01-P`) — **but all flat at level 2** | **Yes** | **Recommended** |

Notes: `pymupdf4llm` renders clause-code hyphens as en-dash (`B4–3.2`, `A1–1–01–P`) — **normalize `–`→`-` before regex-matching numbering.** Its install is heavy (pulls `pymupdf-layout` + `onnxruntime` + Tesseract); the "Using Tesseract for OCR" banner is harmless — these are born-digital, OCR does not run.

### 3.3 Cross-document (tier) relationships
**AKB stands alone** (motor; disjoint `A`…`Q` namespace; no PHV cross-refs). The three **AVB-PHV files are tiers of one private-liability product**: identical IPID and `Teil A`/`Teil B` skeleton, differing in the trailing "Besondere Bedingungen" block, distinguished by a clause-code **suffix** `-B` (Basis) / `-K` (Komfort) / `-P` (Premium). Empirically (suffix-stripped clause stems): **27 stems shared across all three tiers; Komfort adds ~19 over Basis; Premium adds ~18 over Komfort.** Page counts (39/42/45) and bookmark counts (134/155/169) grow monotonically — **Premium ⊃ Komfort ⊃ Basis.** The shared clause-code stem (e.g. `A1-6.14-01`) is the natural cross-document join key, and the IPID even prints the ladder `Basis | Komfort | Premium | Premium Plus` as a comparison column.

---

## 4. Empirical Validation — the Markdown-First Proof

Run offline with `.venv/bin/python` on `AKB.pdf.pdf` (48 pp) and `AVB-PHV-Komfort.pdf.pdf` (42 pp), feeding output to the **pure-Python** `extract_nodes_from_markdown` → `extract_node_text_content` → `build_tree_from_nodes` (md_to_tree's offline core; summaries are the only API-key step and default to `"no"`).

| Path | `#` headers | MD nodes | Tree roots | Max depth |
|---|---|---|---|---|
| **PyPDF2 → md builder** (AKB) | **0** | **0** | **0** | — |
| **PyPDF2 → md builder** (Komfort) | **0** | **0** | **0** | — |
| **pymupdf4llm (default) → md builder** (AKB) | 388 | 388 | 388 | **1 (flat)** |
| **pymupdf4llm (default) → md builder** (Komfort) | 209 | 209 | 209 | **1 (flat)** |
| **pymupdf4llm + numbering heuristic** (AKB) | — | 388 | 264 (64 with children) | **3** |
| **pymupdf4llm + numbering heuristic** (Komfort) | — | 209 | 47 | **2** |

**Before (PyPDF2):**
```
'Geme innützig e Ha ftpﬂ icht-Versicherungsanstal t'      (should be: 'Gemeinnützige Haftpflicht-Versicherungsanstalt')
'GHV VE RSICHERUNG GHV VE RSICHERUNG 06151 3603-0 ...'    (two columns merged)
'...Seite 4Welche Verpflichtungen habe ich?'              (page header bled into body)
→ 0 '#' headers → 0 md-nodes → 0 tree-roots
```

**After (`pymupdf4llm.to_markdown` + numbering heuristic):**
```
## A   Welche Leistungen umfasst Ihre Kfz-Versicherung?
## A.1 Kfz-Haftpflichtversicherung – für Schäden, die Sie mit Ihrem Fahrzeug Anderen zufügen
## A.1.1 Was ist versichert?

Tree (build_tree_from_nodes, with numbering heuristic):
  L0  A    Welche Leistungen umfasst Ihre Kfz-Versicherung?
    L1  A.1   Kfz-Haftpflichtversicherung …
      L2  A.1.1 Was ist versichert?
  → roots: 264, with-children: 64, max-depth: 3
```

**Verdict: hypothesis confirmed.** The markdown-first path turns a 0-node garbled failure into a clean, navigable tree, fully offline, no API key.

**Validation caveats (these define the Tier-0 work item):**
- Default `pymupdf4llm` emits **all** headings at `##` (flat) — titles correct, nesting lost. Real nesting requires a **numbering→level post-pass** (A→L1, A.1→L2, A.1.1→L3; `Teil`→L1, `A1`→L2, `A1-6.3.1`→L3).
- The heuristic remapped 138/388 (AKB) and 162/209 (Komfort) headings; the IPID front-matter is genuinely flat and *correctly* stays at roots.
- `pymupdf4llm` over-segments (bold inline runs like "Teilkasko"/"Vollkasko" become `##`) — inflation, not data loss; the embedded `get_toc()` (~11 lettered AKB parts) is the cross-check oracle.
- One residual ligature artifact survived in a heading (`Kfz-Haftpficht`) — much cleaner than PyPDF2, not 100% perfect.

---

## 5. Solution Evaluation Matrix

Scored against *this* corpus: born-digital, tagged, multi-column German insurance PDFs whose hierarchy lives in numbering, feeding the existing `#`-driven `md_to_tree`.

| Option | Fit to our data | Heading hierarchy | Tables | License / cost | Integration effort | Verdict |
|---|---|---|---|---|---|---|
| **`pymupdf4llm`** | Excellent — fixes garbling + ligatures + columns; already in venv | `##` flat → needs numbering post-pass (small pure-Python) | **Best — structured MD tables (only tool)** | **AGPL-3.0** or paid Artifex *(already a transitive dep)* | **Low** — mirror `.docx` path | **Recommend (Tier 0 primary)** |
| **Plain PyMuPDF blocks + `get_toc()`** | Good text; no MD/tables out-of-box | Synthesize from `get_toc()` (outline is flat) + numbering prefix | Lost as text; `find_tables()` separately | AGPL *(already a dep)* | Medium — write a synthesizer | Fallback if `pymupdf4llm` dropped |
| **Docling (IBM)** — the user's **"DocLink"** | Strong: DocLayNet layout model = correct multi-column reading order; `do_ocr=False` for born-digital; **TableFormer** rebuilds tables | **Flat `##` for PDFs too** (open issues #1023/#1170/#2335) → needs `docling-hierarchical-pdf` add-on or numbering pass | Excellent (TableFormer) | **MIT** (no AGPL); cost is **PyTorch + model downloads** (bigger image, more RAM, slower CPU inference) | Medium-high — new converter + model caching in Docker | **Recommend (Tier 1)** |
| **MarkItDown (Microsoft)** | **Poor** — pdfminer.six, not column/tag-aware; ignores embedded tags | **None** — 0 heading detection (third-party benchmark ~0.000) → degenerate 1-node tree | Weak heuristic | MIT, lightweight | Trivial — but semantically useless for PDFs | **Not recommended** (good only as a `.docx/.html/.xlsx` replacement) |
| **PageIndex OCR (cloud)** | Strong concept — long-context VLM preserves hierarchy across pages; emits MD + node tree | Good (purpose-built) | Good | **Paid SaaS** (1 credit/page; 200-credit free trial covers all 4 docs) | Medium — new cloud client in arq worker | **Situational** — German accuracy *undocumented*; GDPR/data-residency for insurance content |
| **GraphRAG / LazyGraphRAG (Microsoft)** | **Wrong tool for 4–7 docs** — entity-graph + community summaries are for large entity-rich corpora; underperforms on single-hop clause lookup | N/A (replaces the whole stack) | N/A | MIT software; LLM indexing cost (LazyGraphRAG ≈ vector-RAG cost) | Very high — parallel stack, no PageIndex tree | **Not recommended** (full); see Tier 2 lightweight alternative |
| **Obsidian + wikilinks** | Free human-inspectable visualization of the clause graph | N/A | N/A | Free | Export-only | **Situational** — nice-to-have QA/visualization layer over the graph |

### Resolving the user's specific hypotheses
- **"Docling / DocLink"** → This is **IBM Docling**, and it is a *good Tier-1 choice*: its DocLayNet layout model fixes the exact column-bleed root cause and TableFormer reconstructs the SF-Klasse tables. **But** — same as `pymupdf4llm` — its PDF→Markdown export **flattens all headings to `##`** (open, unresolved upstream). So Docling does **not** by itself hand `md_to_tree` a usable hierarchy; it needs either the unofficial `docling-hierarchical-pdf` add-on or the same numbering post-pass. It is MIT (no AGPL) but pulls PyTorch + auto-downloaded models.
- **MarkItDown** → **No.** Its default PDF path is pdfminer.six raw text with **zero** heading detection; `md_to_tree` would collapse output to a single useless node. It *does* fix ligatures, but a clean flat blob is still worthless to a `#`-driven tree builder. Keep it in mind only to replace the LibreOffice/`html_to_markdown` branches for *non-PDF* formats.
- **GraphRAG** → **No, not as the RAG engine.** It owns the entire index+query stack, can't emit a PageIndex tree, costs LLM money at index time, and is built for *global* multi-hop questions across a *large* corpus — the opposite of single-hop insurance clause lookup on 4–7 docs. The *valuable* idea (cross-tier comparison) is better served by a thin `networkx` graph layer (Tier 2).
- **Obsidian** → A **free visualization/QA aid**, not a retrieval engine. Export the same cross-doc graph as markdown + `[[wikilinks]]` for domain experts to inspect the clause graph; it does not replace the per-doc tree RAG.

---

## 6. Recommendation — Tiered Plan

### Tier 0 — Quick (hours): route `.pdf` through markdown-first

Two viable sub-options; **Option B is recommended** because it also fixes RC3 (TOC fragility), not just RC1.

**Option A — fork the user-owned library to thread `pdf_parser` (fixes RC1/RC2 only):**
1. `config.yaml`: add `pdf_parser: PyMuPDF` (must be declared — `_validate_keys` at `utils.py:665` rejects unknown keys; `opt` is a `SimpleNamespace`).
2. `page_index.py:1077`: `get_page_tokens(doc, model=opt.model, pdf_parser=getattr(opt, "pdf_parser", "PyPDF2"))`.
3. *(optional per-call override)* add `pdf_parser=None` to `page_index()` (`page_index.py:1113`); it auto-flows into `user_opt` via the `locals()` comprehension (`1115-1118`).
4. Add NFKC normalization for residual ligatures.

   ⚠️ This still hits `process_no_toc` (RC3) on any doc without a printed page-numbered TOC, and yields a flat tree (font-size headings can't separate clauses). **Insufficient alone for these docs.**

**Option B (recommended) — PDF → structured markdown → existing `_run_md_to_tree`:**

In `src/pageindex_mcp/client.py`, change the `.pdf` branch (`client.py:94`) to mirror the existing `.docx`/`.html` pattern (`client.py:117-124, 259-280`):

```python
# converters.py — new
def pdf_to_markdown(pdf_path: str) -> str:
    import pymupdf4llm, re
    md = pymupdf4llm.to_markdown(pdf_path)          # clean text + tables + ## headings
    md = md.replace("–", "-")                  # en-dash → hyphen (clause codes)
    return _relevel_headings(md)                    # numbering → '#' depth (see below)

# _relevel_headings: pure-Python, no API key
#   AKB:     A -> #,  A.1 -> ##,  A.1.1 -> ###,  A.1.1.1 -> ####
#   AVB-PHV: Teil X -> #,  A1/B4 -> ##,  A1-6.3.1 / B4-3.2 -> ###,  deeper dotted -> ####
#   keep Cover/IPID/Body/Besondere-Bedingungen front-matter headings as level-1 siblings
#   cross-check derived levels + page anchors against doc.get_toc()
```

```python
# client.py  .pdf branch
if ext == ".pdf":
    try:
        md = pdf_to_markdown(file_path)
        with NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(md); tmp = f.name
        return await self._run_md_to_tree(tmp)      # robust, no TOC detection
    except Exception:
        return await self._run_page_index(file_path)  # keep as fallback
```

This sidesteps **both** PyPDF2 garbling (RC1) and LLM TOC detection (RC3), reuses all dedup/storage/queue code unchanged, and is validated above to produce a depth-3 tree.

> **AGPL caveat:** `pymupdf4llm` is AGPL-3.0 (or paid Artifex). For a network-served product, AGPLv3 §13 can require offering source to users interacting over the network. **This obligation already exists today** because `pymupdf>=1.27.2.2` is a direct dependency — adopting `pymupdf4llm` adds *no new* license exposure. If AGPL is unacceptable, either buy an Artifex commercial license **or** use the MIT-licensed **Docling** path (Tier 1).

**Also in Tier 0 (cheap, independent):**
- Add a `validate_tree(result)` gate before `save_doc` (see §7, P0b) — pure-Python, catches the silent-empty-tree failure.
- Copy the four files into `doc_store/` (or `POST /upload/files`) so they're actually ingested (RC5).
- Repoint `upload.py` at `POST /upload/files` (or re-register a `process_document` tool) and correct the stale `CLAUDE.md` tool claims (RC6).

### Tier 1 — Robust (days): high-fidelity converter + fallback chain
- Add a **Docling**-based converter (`do_ocr=False`, MIT, TableFormer for the AKB Anhang tables) as either the primary high-fidelity path or a fallback when `pymupdf4llm` heading recovery is weak. Restore heading levels via `docling-hierarchical-pdf` (bookmarks → numbering → font/bold) **or** the same numbering post-pass.
- **Fallback chain:** `pymupdf4llm` (+ numbering) → Docling (+ hierarchy add-on) → plain PyMuPDF `get_text('dict')` + `get_toc()` synthesizer → `_run_page_index` (legacy, last resort).
- **Seed/validate against `doc.get_toc()`** for every doc (23/134/155/169 entries with level+page+title) — the embedded outline is a free structural oracle and page-anchor source.
- Extract AKB **Anhang 1–3** tables separately via `page.find_tables()` and attach as structured **table leaf nodes** under their numbered parent. *(Note: PHV IPID `find_tables` hits are layout artifacts — treat IPID + all conditions bodies as prose.)*
- Make the tree **structure-aware**: persist node metadata `product`, `tier` (`-B/-K/-P`), `clause_id` + suffix-stripped `clause_stem` (the cross-tier join key), `part_type` (IPID/conditions/appendix) + `binding` (so the non-binding IPID is never cited as the legal answer), `page_range`, `amends_ref` (from "abweichend von A1-1"), `node_kind`.

*(If AGPL is a hard blocker and you want zero local extraction risk, **PageIndex OCR cloud** is the situational alternative here — but validate German accuracy on these exact PDFs first and clear the GDPR/data-residency question for insurance content.)*

### Tier 2 — Strategic (weeks): cross-document graph/diff layer + automated ingestion
- Add `src/pageindex_mcp/graph.py`: consume the per-doc trees, build a `networkx` graph — nodes = clauses (keyed by normalized heading + `clause_stem`), edges = `contains` (existing hierarchy) + `tier-variant-of` (same `clause_stem` across Basis/Komfort/Premium) + `amends`/`references` (parsed cross-refs). Persist `processed/graph.json` next to the trees in MinIO.
- A **tier-diff pass** over `tier-variant-of` pairs surfaces the upsell deltas (clauses present in Komfort but not Basis; changed Versicherungssummen).
- Expose new MCP tools alongside the existing five: `compare_tiers(clause)` and `find_clause_across_docs(query)`. The graph is **additive** — per-doc tree RAG keeps serving single-doc/single-hop lookups; the graph handles only cross-tier comparison and multi-hop reference-following.
- Optionally export the graph as Obsidian markdown + `[[wikilinks]]` for free expert QA/visualization.
- **Do not** adopt full Microsoft GraphRAG — for this corpus size it is overkill, costs LLM money at index time, can't emit a PageIndex tree, and underperforms on the dominant single-hop clause-lookup query. (LazyGraphRAG removes the *cost* objection but not the *architecture mismatch*.)

---

## 7. Scalability & Automation Architecture

Everything below reuses the existing **FastMCP + arq + MinIO + Redis + Prometheus** stack — **no new infra, no vector DB.** Verified pipeline: `POST /upload/files` (X-API-Key) → stage to MinIO `uploads/staging/<job_id>/` → `arq enqueue process_document_job` → `worker.process_document_job` → `client.index()` → persist `processed/<doc_id>.json` + `.meta.json` + raw + `hashes/processed_hashes.json`. Retrieval: `find_relevant_documents` → `_prefilter_docs` (1 cheap `gpt-4o-mini` call, skipped if ≤1 doc) → `_search_one_doc` under `asyncio.Semaphore(PAGEINDEX_SEARCH_CONCURRENCY=3)` (1 search call/surviving doc) → excerpts.

**P0 — do first (zero new infra):**
- **P0a Ingest quality** — Tier-0 Option B (PDF → structured markdown → `_run_md_to_tree`). Inherits all dedup/storage/queue code unchanged.
- **P0b QA gate** — add `validate_tree(result)` in `client.index()` **before** `save_doc`: assert `node_count >= 3` (for a 40-pp doc), `depth >= 2`, and a garbling heuristic (high ratio of single-char/space-broken tokens or unmatched ﬂ/ﬁ in node titles). On failure: **do not silently persist** — set arq job `status="error"`, reason `low_quality_tree` (surfaced via `GET /upload/status/{job_id}`), increment a new Prometheus counter `LOW_QUALITY_TREES`. Pure-Python, no API key. *(This is the single most important operational fix — today `client.index()` only logs `len(structure)` and persists empty trees silently.)*
- **P0c Harden the worker** — `WorkerSettings` currently sets only functions/startup/shutdown/redis (so arq defaults: `max_jobs=10`, `job_timeout=300s`, `max_tries=5`, no cron, no DLQ). Set `job_timeout=900` (large-PDF indexing > 300s would be killed), `max_tries=2` (don't re-run deterministic indexing failures 5×), and on final failure push `staging_key + error` to a Redis `pageindex:dlq` list.

**P1 — automation & versioning:**
- **P1a Watch-folder / event ingest** — configure a MinIO bucket notification (`notify_redis`/`notify_webhook`) on `s3:ObjectCreated` under an `inbox/` prefix → a tiny consumer (or a new `/upload/from-storage` route) calls `arq enqueue_job`. Zero-config alternative: an arq **cron_job** that sweeps `inbox/` every N minutes and enqueues unseen keys — idempotent because the SHA-256 dedup already no-ops unchanged files.
- **P1b Versioning** (the yearly-reissue problem — AKB is the **2026** motor edition; AVB-PHV-Basis is the **2023** liability edition) — add `effective_date` + `doc_family` to the meta sidecar so reissues chain; keep old + new (compliance: answer "as of date X") but make `_prefilter_docs`/`_rag` prefer the latest `effective_date` per family, with an optional `as_of_date` filter. Change the dedup key from **filename**→sha256 to **content-hash within family scope** (today identical bytes under a different name re-index, and a reissue creates an unrelated `doc_id` with no `supersedes` link).
- **P1c Cost/latency** — keep filter+search on `gpt-4o-mini`-class; safely bump `PAGEINDEX_SEARCH_CONCURRENCY` to ~8–10 (it's just a Semaphore width) within OpenAI rate limits; add a small Redis result-cache (`query-hash → excerpts`, short TTL) reusing `cache.py`.

**P2 — evaluation loop:**
- Maintain a small golden-question set per family (5–10 Q/expected-clause pairs for AKB and each AVB tier); a nightly arq cron runs `find_relevant_documents` and asserts the expected node/text is retrieved; emit pass-rate as a Prometheus gauge and alert on regression. P0b is the cheap structural pre-check; this is the semantic backstop (RAGAS/LLM-as-judge as an optional deeper pass).

**Scaling summary:** ingest scales by adding stateless arq worker replicas + tuning `max_jobs`; queries scale by gunicorn pod replicas behind Traefik **sticky sessions** (`WEB_CONCURRENCY` must stay **1** — MCP sessions are in-memory per worker) + bumping `PAGEINDEX_SEARCH_CONCURRENCY` + the Redis caches. The structural QA gate and cron-sweep ingestion need **no API key at all**.

---

## 8. Concrete Next Steps (ordered checklist)

1. **Add `pdf_to_markdown` + `_relevel_headings`** to `src/pageindex_mcp/converters.py` (pymupdf4llm → en-dash normalize → numbering→`#` depth; cross-check `doc.get_toc()`).
2. **Re-route `.pdf`** in `client.py:94` to `pdf_to_markdown → temp .md → _run_md_to_tree`, keeping `_run_page_index` as a `try/except` fallback.
3. **Add `validate_tree()`** before `save_doc` (node_count/depth/garbling asserts) → arq `status="error"`/`low_quality_tree` + `LOW_QUALITY_TREES` Prometheus counter.
4. **Re-index the four `issue/data/*.pdf.pdf`** (copy to `doc_store/` + `preprocess_client.py`, or `POST /upload/files`) and confirm non-flat trees + clean German titles.
5. **Harden `WorkerSettings`** — `job_timeout=900`, `max_tries=2`, Redis DLQ.
6. **Fix `upload.py`/`CLAUDE.md`** (RC6) — repoint at `/upload/files`; correct stale tool claims.
7. **Decide the license posture** — accept AGPL (already incurred via `pymupdf`), buy Artifex, or pivot the converter to MIT **Docling** (then cache its models at Docker build time).
8. **Tier 1:** add Docling converter + fallback chain + `get_toc()` seeding + AKB `find_tables()` table nodes + structure-aware node metadata (`product/tier/clause_stem/part_type/binding`).
9. **Tier 2:** build `graph.py` (networkx cross-tier graph + diff), add `compare_tiers`/`find_clause_across_docs` MCP tools, add versioning (`effective_date`/`doc_family`, content-hash dedup), watch-folder ingest, golden-question eval cron.

---

## 9. Open Questions / Risks

- **Heading re-leveling robustness.** The numbering→level heuristic remapped only 138/388 (AKB) and 162/209 (Komfort) headings; subsection headings lacking an adjacent code stay flat. Cross-validating against `get_toc()` is the mitigation — but the AVB outlines are themselves flat (all level 1), so the *numbering prefix* remains the primary depth signal. Budget iteration on the AVB clause-code grammar (`Teil`/`A1`/`A1-6.3.1`/`A1-1-01-P`).
- **`pymupdf4llm` over-segmentation** (bold inline runs → spurious `##`) inflates node counts vs a human TOC. Acceptable for retrieval, but may need a min-length / non-clause-prefix filter to suppress noise nodes.
- **Residual ligature artifacts** (`Kfz-Haftpficht`) survive occasionally even on the clean path — add NFKC normalization and consider a small German-aware fix-up dictionary for known insurance terms.
- **AGPL exposure** is a *legal* decision, not a technical one. It already exists; adopting `pymupdf4llm` doesn't worsen it, but if the product is distributed/network-served to external customers, get sign-off or pivot to Docling.
- **Docling/PageIndex-OCR footprint** — Docling adds PyTorch + model downloads (image size, RAM, slower CPU inference, build-time model caching). PageIndex OCR adds a paid SaaS dependency, network latency, **undocumented German accuracy**, and a **GDPR/data-residency** question for insurance content sent to `api.pageindex.ai`.
- **Tables vs `#`-tree.** `pymupdf4llm` renders tables as pipe tables that `md_to_tree`'s `#`-scanner treats as *node text*, not structure — fine for retrieval, but table-cell context is flattened. Tier-1 `find_tables()` table nodes address this; retrieval quality on tables is **untested**.
- **Tier-alignment brittleness.** Same-`clause_stem` matching across tiers can mis-align if a tier renumbers/merges clauses; German cross-reference parsing ("siehe Ziffer", "abweichend von", "im Sinne von") is heuristic and needs curation.
- **Versioning semantics** — reissues currently create unrelated `doc_id`s with no `supersedes` link or `effective_date`, so retrieval can mix a 2023 and a 2026 clause with no "current" preference. P1b is required before the corpus accumulates multiple editions.
