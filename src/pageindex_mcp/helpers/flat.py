"""FLAT-01: deterministic flat-document classifier + block extractor."""

from __future__ import annotations

import logging
import re

from ..metrics import FENCE_PARITY_WARNING
from .table_stitch import (
    _looks_like_toc_page,
    flag_empty_cells,
    stitch_continuation_tables,
)
from .tables import _flat_is_pipe_row, _flat_is_separator_row, _flat_parse_table

logger = logging.getLogger(__name__)


_FLAT_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_FLAT_NUMBERED_RE = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?(?:\s+\S.*)?$")
_FLAT_FIGURE_RE = re.compile(r"^\[Figure:\s*fig-(\d+)(?:\s*\|\s*(.*?))?\]$")
_FLAT_RAW_IMAGE_RE = re.compile(r"^<!--\s*image\s*-->$")
_FLAT_CHART_TEXT_RE = re.compile(r"^>\s*\[Chart text\]:\s*(.+)$")


def route_and_extract_flat(md: str) -> tuple[str, list[dict]]:  # noqa: C901, PLR0915
    """FLAT-01-C1/C2/C3: classify a flat (no-hierarchy) markdown document and
    extract role-typed blocks.

    Returns (content_class, blocks) where content_class is one of
    'flat_table', 'flat_kv', 'flat_prose', 'flat_mixed'."""
    blocks: list[dict] = []
    signals: set[str] = set()

    lines = (md or "").splitlines()
    prose_buf: list[str] = []

    def flush_prose() -> None:
        if prose_buf:
            text = " ".join(p.strip() for p in prose_buf).strip()
            if text:
                blocks.append({"role": "prose", "text": text})
                signals.add("prose")
            prose_buf.clear()

    _fence_depth = 0

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if stripped == "```":
                _fence_depth -= 1
                if _fence_depth < 0:
                    logger.warning(
                        "fence_parity: orphan close at line %d "
                        "(content preserved per RFC-030 D0, observability only)",
                        i + 1,
                    )
                    FENCE_PARITY_WARNING.labels(kind="orphan_close").inc()
                    _fence_depth = 0
            else:
                _fence_depth += 1
            i += 1
            continue

        if stripped == "":
            flush_prose()
            i += 1
            continue

        if (
            not prose_buf
            and stripped
            and all(c == stripped[0] for c in stripped)
            and stripped[0] in "-=*"
            and len(stripped) >= 3
        ):
            i += 1
            continue

        if _flat_is_pipe_row(line) and i + 1 < n and _flat_is_separator_row(lines[i + 1]):
            flush_prose()
            table_start = i
            block, i = _flat_parse_table(lines, table_start)
            raw_table_text = "\n".join(lines[table_start:i])
            if _looks_like_toc_page(raw_table_text):
                blocks.append({"role": "prose", "text": raw_table_text})
                signals.add("prose")
            else:
                blocks.append(block)
                signals.add("table")
            continue

        m_fig = _FLAT_FIGURE_RE.match(stripped)
        if m_fig:
            flush_prose()
            fig_index = int(m_fig.group(1))
            fig_desc = (m_fig.group(2) or "").strip()
            ocr_text = ""
            j = i + 1
            while j < n and lines[j].strip() == "":
                j += 1
            if j < n:
                m_ct = _FLAT_CHART_TEXT_RE.match(lines[j].strip())
                if m_ct:
                    ocr_text = m_ct.group(1).strip()
                    i = j + 1
                else:
                    i += 1
            else:
                i += 1
            img_block: dict = {"role": "image", "index": fig_index}
            if ocr_text:
                img_block["ocr_text"] = ocr_text
            if fig_desc:
                img_block["description"] = fig_desc
            blocks.append(img_block)
            continue

        if _FLAT_RAW_IMAGE_RE.match(stripped):
            flush_prose()
            blocks.append({"role": "image"})
            i += 1
            continue

        m_head = _FLAT_HEADING_RE.match(line)
        if m_head:
            flush_prose()
            blocks.append({"role": "title", "text": m_head.group(1).strip()})
            i += 1
            continue

        if _FLAT_NUMBERED_RE.match(line):
            flush_prose()
            blocks.append({"role": "kv", "text": stripped})
            signals.add("kv")
            i += 1
            continue

        prose_buf.append(stripped)
        i += 1

    flush_prose()

    if _fence_depth > 0:
        logger.warning(
            "fence_parity: %d unclosed fence delimiter(s) at EOF "
            "(content preserved per RFC-030 D0, observability only)",
            _fence_depth,
        )
        FENCE_PARITY_WARNING.labels(kind="unclosed_at_eof").inc()

    blocks = stitch_continuation_tables(blocks)
    for block in blocks:
        if block.get("role") == "table":
            flag_empty_cells(block)

    content_signals = signals & {"table", "kv", "prose"}
    if len(content_signals) > 1:
        content_class = "flat_mixed"
    elif content_signals == {"table"}:
        content_class = "flat_table"
    elif content_signals == {"kv"}:
        content_class = "flat_kv"
    else:
        content_class = "flat_prose"

    return content_class, blocks


def _flat_block_primary_text(block: dict) -> str:
    """D0 (RFC-027): a single flat block's primary document text, excluding
    OCR/description enrichment metadata."""
    text = block.get("text", "")
    if text:
        return text
    role = block.get("role")
    if role == "table":
        return "\n".join(block.get("row_records", []) or [])
    return text


def _flat_search_text(data: dict) -> str:
    """FLAT-05-C1 helper: render a flat doc's verbalized content as a single
    retrieval string."""
    parts: list[str] = []
    for block in data.get("blocks", []) or []:
        role = block.get("role")
        if role == "table":
            parts.extend(block.get("row_records", []) or [])
        elif role == "image":
            ocr = block.get("ocr_text")
            if ocr:
                parts.append(ocr)
            desc = block.get("description")
            if desc:
                parts.append(desc)
        else:
            txt = block.get("text")
            if txt:
                parts.append(txt)
    for rec in data.get("row_records", []) or []:
        if rec not in parts:
            parts.append(rec)
    return "\n".join(parts)


def flat_doc_view(data: dict) -> dict | None:
    """FLAT-05-C2: build the get_document / get_document_structure response shape
    for a flat doc."""
    content_class = data.get("content_class")
    if not content_class:
        return None

    blocks = data.get("blocks", []) or []
    # Pre-aggregated row_records (written at ingestion by _persist_flat_result)
    # takes precedence; fall back to block-iteration derivation for documents
    # persisted before the pre-aggregation change.
    pre_agg = data.get("row_records")
    if pre_agg is not None:
        row_records: list[str] = list(pre_agg)
    else:
        row_records = []
        for block in blocks:
            if block.get("role") == "table":
                row_records.extend(block.get("row_records", []) or [])

    return {
        "doc_name": data.get("doc_name", data.get("filename", "")),
        "content_class": content_class,
        "blocks": blocks,
        "row_records": row_records,
        "structure": [],
        "doc_description": data.get("doc_description", ""),
    }
