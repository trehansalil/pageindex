---
zone_name: Converter Chain Fallback / AGPL Gating
severity: medium
bug_count: 2
status: improved
audit_date: 2026-08-27
audit_run: POST-RUN20
audit_source: audit/ARCHITECTURE_DEFECT_ZONES_AUDIT_2026-08-27_POST-RUN20.md
key_files:
  - src/pageindex_mcp/converters/pipeline.py
  - src/pageindex_mcp/client/indexer.py
  - src/pageindex_mcp/config.py
tags:
  - zone-spec
  - medium
  - converter
  - agpl
  - legal
scorecard_verdict: needs_another_cycle
scorecard_date: 2026-08-27
scorecard_run: POST-RUN20
---
## Mechanism

pdf_markdown_converters() builds an ordered converter chain gated by allow_agpl_fallback and PDF_CONVERTER. When the primary converter (Docling) fails or times out, the for-loop in _convert_to_tree walks to the next chain entry unconditionally. If that entry is pymupdf4llm (AGPL-3.0), an unplanned outage silently becomes AGPL-licensed network-served conversion. The allow_agpl_fallback flag now blocks pymupdf4llm from the chain entirely when false, and AGPL_FALLBACK_TOTAL metrics track when it fires. However, the chain is a flat list with no policy for 'which fallbacks are acceptable for which failure modes' — a timeout is treated identically to a parse error. Converter provenance (extraction_route, converter_name) is now persisted in the sidecar via _MERGE_FIELDS, but historical corpus documents lack this data.

The generative mechanism operates through unconditional chain-walking on converter failure:
- a. When remote Docling raises HTTP 504 on a large PDF, _convert_to_tree walks to pymupdf4llm — an AGPL route the operator may not have intended, violating HR4's framing as 'a legal decision to clear, not a settled safe-harbor' (chain 6).
- b. The remote Docling service runs a separately-deployed image that may predate local fixes, so converter-level fixes have no effect on documents routed through the remote path (shared with bidi zone).
- c. The underlying structural issue is that the chain treats all failures equivalently: a transient network timeout and a fundamental parsing incompatibility both trigger the same fallback path.

## Code Evidence

`pdf_markdown_converters` at converters/pipeline.py:571-641: `if pipeline_config.allow_agpl_fallback: chain.append(("pymupdf4llm", _pdf_to_markdown_no_pics, False))` then docling inserted at position 0 or appended based on PDF_CONVERTER. `_convert_to_tree` at client/indexer.py:435-540: `chain = pdf_markdown_converters()` then `for idx, (conv_name, conv_fn, _conv_supports_ocr) in enumerate(chain): try: ... except Exception as conv_exc: md_content = None`. AGPL_FALLBACK_TOTAL.labels(reason='fired').inc() at indexer.py:~580 when used_converter == 'pymupdf4llm' and not primary.

## Key Files

| File | Role |
|---|---|
| src/pageindex_mcp/converters/pipeline.py | Converter chain construction |
| src/pageindex_mcp/client/indexer.py | Chain-walking on failure |
| src/pageindex_mcp/config.py | AGPL gating configuration |
