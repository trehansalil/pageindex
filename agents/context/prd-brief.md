# PRD Brief — digest of RESEARCH.md for the PRD builder

> Source: RESEARCH.md (§Market & Design + §Domain highlights). Do NOT read RESEARCH.md whole.
> Read IdeasV2.md directly for the product spec / remediation plan; this brief only carries the
> research-grounded positioning, competitive, and quality-bar facts the PRD must respect.

## Product positioning — the one rule that overrides marketing instinct

**DO NOT pitch "vectorless RAG beats vector RAG" on accuracy.** Three quantitative
"vectorless/tree beats vector" benchmark claims (cross-reference recall 100% vs 91.7%; legal
0.883 / medical 0.933 dominance; FinanceBench 0.938) were **REFUTED** under 3-vote adversarial
review (votes 0-3, 1-2, 0-3). The source did not hold up. Any PRD success-metric or marketing
line that asserts accuracy superiority is unsupported and must not ship. **[high confidence that
these numbers are unreliable]**

**Position on ARCHITECTURAL merits instead** (defensible, verified-or-reasoned):
- **No vector DB to operate** — lower infra surface than embedding-RAG competitors.
- **Transparent, inspectable hierarchical trees** as the retrieval substrate (vs opaque embedding spaces) — auditable, debuggable.
- **Structural-query alignment** — for documents whose meaning lives in hierarchy (insurance T&C, legal, contracts), tree-search matches the document's own logic.
- Motivation is primary-sourced: standard RAG retrieves "short contiguous chunks … limiting holistic understanding," which is why hierarchical methods exist (RAPTOR, arXiv 2401.18059, ICLR 2024). **But** RAPTOR is *tree-organized yet embedding-dependent* — use it as the "tree-but-still-vector" contrast, NOT as evidence for vectorless retrieval.

## Competitor landscape (positioning only — no benchmark superiority claims)

Field: LlamaParse, Unstructured.io, Docling, Azure AI Document Intelligence, AWS Textract,
Reducto, PageIndex cloud OCR. Differentiation for a **self-hosted vectorless-tree** product =
the three architectural merits above. Per-competitor pricing/strength/gap tables were **not
verified** — flag any competitive matrix in the PRD as a market-research gap, don't fabricate numbers.

## Target & validation corpus

- Product target = **arbitrary document corpora** (generic ingestion).
- Validation corpus = **German insurance T&C PDFs** (born-digital, tagged; hierarchy lives in
  alphanumeric clause numbering + embedded bookmark TOC, not font size). The PRD should frame the
  insurance corpus as the *proving ground / first vertical*, not the product's scope ceiling.
- A genuine, verified user value in the corpus: the 3 AVB-PHV files are **tiers of one product**
  (Basis ⊂ Komfort ⊂ Premium, shared clause-code stems). "What does Komfort add over Basis?" is a
  cross-document question no single-doc tree answers — a real differentiating feature (cross-tier compare).

## Quality bar = product requirement (this is the a11y axis)

For this product, accessibility = **machine-consumability of outputs**: downstream LLM agents need
clean structured trees with correct depth and faithful tables. A flattened, garbled, or
over-segmented tree is an *inaccessible* output. So these become acceptance criteria, not nice-to-haves:
- **Depth recovery**: a doc with an outline TOC or numbering prefixes must yield depth ≥ 2 (all-`h2`
  flat tree = the font-size flattener fired = a defect).
- **No garbling**: residual ligature glyphs (ﬁ/ﬂ/ﬃ) or abnormal intra-word-space ratios in node text = fail.
- **No over-segmentation**: implausibly high heading-node count vs page count = bold-run promotion bug.
- These thresholds are **PROPOSED, not empirically calibrated** — the PRD should require calibrating
  them against the German-insurance corpus before they gate releases. Don't present them as settled.

## DX / operability requirements (this is a backend/MCP product — "UX" = API/DX)

- Tool naming is already verb-noun, query-shaped (`recent_documents`, `find_relevant_documents`,
  `get_document`, `get_document_structure`, `get_page_content`) — keep the convention; new tools
  (`compare_tiers`, `find_clause_across_docs`) must match it.
- Async ingest = `POST /upload/files` → poll `GET /upload/status/{job_id}`; pair with content-hash
  dedup so re-uploads are idempotent.
- **Erasure / DSR is a first-class product capability** (compliance, see arch-brief): a deletion must
  cascade across every derived store; raw-file deletion does NOT auto-cascade (refuted). The PRD must
  list "right-to-erasure that purges source + derived index + cache + (documented) backups" as a requirement.
- Observability: Prometheus metrics + health endpoints; surface a `low_quality_tree` failure to the
  job-status API rather than silently persisting a bad tree.

## Confidence / honesty notes for the PRD

- Refuted (do not build on): vectorless accuracy-superiority numbers; "deleting source auto-removes derivatives."
- Thin/unverified (flag as risks, don't assert): Docling runtime footprint; competitor pricing; quality-gate threshold values; FastMCP multi-worker behavior.
- Time-sensitive: provider data-handling tiers and package versions (current as of 2026-05).
