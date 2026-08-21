"""Table stitching (continuation-table merging) and ToC-node stripping."""

from __future__ import annotations

import copy
import logging
import os
import re

from ..metrics import TOC_STRIP_HIGH_CHAR_LOSS, TOC_STRIP_SKIPPED
from .tables import _flat_verbalize_rows
from .tree_split import _is_numeric_or_date, table_is_rtl

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fix 2a: continuation-table stitching
# ---------------------------------------------------------------------------


def _is_continuation_table(anchor: dict, cont: dict) -> bool:
    """A later table block continues `anchor` when it has the same number of data
    rows, `anchor` itself is a keyed table (at least one non-numeric row-label
    header), AND all of `cont`'s headers are date/numeric-like."""
    a_data = (anchor.get("rows") or [])[1:]
    c_data = (cont.get("rows") or [])[1:]
    if len(a_data) != len(c_data) or not c_data:
        return False
    a_headers = anchor.get("headers") or []
    if not a_headers or not any(not _is_numeric_or_date(h) for h in a_headers):
        return False
    c_headers = cont.get("headers") or []
    if not c_headers:
        return False
    return all(_is_numeric_or_date(h) for h in c_headers)


def _merge_continuation_table(anchor: dict, cont: dict) -> dict:
    """Left-key on the anchor's row-label column and concatenate the
    continuation's data columns onto each row. RTL-aware."""
    a_headers = list(anchor.get("headers") or [])
    c_headers = list(cont.get("headers") or [])
    a_data = (anchor.get("rows") or [])[1:]
    c_data = (cont.get("rows") or [])[1:]

    if table_is_rtl(anchor):
        label_idx = [k for k, h in enumerate(a_headers) if not _is_numeric_or_date(h)]
        date_idx = [k for k, h in enumerate(a_headers) if _is_numeric_or_date(h)]
        merged_headers = (
            [a_headers[k] for k in label_idx] + c_headers + [a_headers[k] for k in date_idx]
        )
        merged_data: list[list[str]] = []
        for ar, cr in zip(a_data, c_data, strict=False):
            labels = [ar[k] if k < len(ar) else "" for k in label_idx]
            dates = [ar[k] if k < len(ar) else "" for k in date_idx]
            merged_data.append([*labels, *cr, *dates])
    else:
        merged_headers = [*a_headers, *c_headers]
        merged_data = [[*ar, *cr] for ar, cr in zip(a_data, c_data, strict=False)]

    return {
        "role": "table",
        "headers": merged_headers,
        "rows": [merged_headers, *merged_data],
        "row_records": _flat_verbalize_rows(merged_headers, merged_data),
    }


def stitch_continuation_tables(blocks: list[dict]) -> list[dict]:
    """Fix 2a: merge wide tables paginated across pages back together."""
    result: list[dict] = []
    i = 0
    n = len(blocks)
    while i < n:
        block = blocks[i]
        if block.get("role") != "table":
            result.append(block)
            i += 1
            continue
        anchor = block
        j = i + 1
        while (
            j < n and blocks[j].get("role") == "table" and _is_continuation_table(anchor, blocks[j])
        ):
            anchor = _merge_continuation_table(anchor, blocks[j])
            j += 1
        result.append(anchor)
        i = j
    return result


def flag_empty_cells(block: dict) -> dict:
    """Fix 2c: annotate a table block with an empty-cell quality signal."""
    data_rows = (block.get("rows") or [])[1:]
    total = 0
    empty = 0
    for row in data_rows:
        for cell in row:
            total += 1
            if str(cell).strip() == "":
                empty += 1
    empty_cell_ratio = (empty / total) if total else 0.0

    suspected_miss = False
    for row in data_rows:
        if row and all(str(c).strip() == "" for c in row):
            suspected_miss = True
            break
    if data_rows and not suspected_miss:
        ncol = max(len(r) for r in data_rows)
        for c in range(ncol):
            col = [row[c] for row in data_rows if c < len(row)]
            if col and all(str(x).strip() == "" for x in col):
                suspected_miss = True
                break

    block["quality"] = {
        "empty_cell_ratio": empty_cell_ratio,
        "suspected_miss": suspected_miss,
    }
    return block


# ---------------------------------------------------------------------------
# RFC-034 D11: ToC heading-node stripping
# ---------------------------------------------------------------------------

_TOC_DOT_LEADER_RE = re.compile(r"\.{4,}\s*\d+\s*\|?\s*$")


def _strip_toc_heading_nodes(nodes: list[dict]) -> list[dict]:
    """RFC-034 D11: remove nodes whose text is empty or consists only of ToC
    dot-leader lines, where the title also looks like a ToC entry."""
    result = []
    for node in nodes:
        text = (node.get("text") or "").strip()
        title = (node.get("title") or "").strip()
        text_lines = [ln for ln in text.splitlines() if ln.strip()]
        if (not text_lines or all(_TOC_DOT_LEADER_RE.search(ln) for ln in text_lines)) and (
            _TOC_DOT_LEADER_RE.search(title) or not title
        ):
            continue
        if "nodes" in node:
            node["nodes"] = _strip_toc_heading_nodes(node["nodes"])
        result.append(node)
    return result


_TOC_STRIP_MAX_CHAR_LOSS_RATIO: float = float(
    os.environ.get("TOC_STRIP_MAX_CHAR_LOSS_RATIO", "0.15")
)
_TOC_STRIP_CHAR_LOSS_WARN_THRESHOLD: float = 0.10


def _strip_toc_heading_nodes_guarded(nodes: list[dict], doc_name: str = "") -> list[dict]:
    """RFC-034 D16: guard D11's `_strip_toc_heading_nodes` against over-stripping."""
    from .tree_validation import _flatten_tree_text, _tree_depth, _tree_node_count

    depth_before = _tree_depth(nodes)
    count_before = _tree_node_count(nodes)
    text_before = _flatten_tree_text(nodes)
    chars_before = len(text_before)

    candidate = _strip_toc_heading_nodes(copy.deepcopy(nodes))

    depth_after = _tree_depth(candidate)
    count_after = _tree_node_count(candidate)
    text_after = _flatten_tree_text(candidate)
    chars_after = len(text_after)

    char_loss_ratio = 1.0 - (chars_after / chars_before) if chars_before > 0 else 0.0

    logger.info(
        "toc_strip: %s depth %d->%d, nodes %d->%d, chars %d->%d, char_loss_ratio=%.4f",
        doc_name,
        depth_before,
        depth_after,
        count_before,
        count_after,
        chars_before,
        chars_after,
        char_loss_ratio,
    )

    if char_loss_ratio > _TOC_STRIP_CHAR_LOSS_WARN_THRESHOLD:
        TOC_STRIP_HIGH_CHAR_LOSS.inc()

    depth_delta = depth_before - depth_after
    depth_guard = depth_delta > 1 and depth_after < 2
    node_guard = count_before > 0 and (count_before - count_after) / count_before > 0.20
    char_guard = char_loss_ratio > _TOC_STRIP_MAX_CHAR_LOSS_RATIO

    if depth_guard or node_guard or char_guard:
        reasons = []
        if depth_guard:
            reasons.append(f"depth {depth_before}->{depth_after}")
        if node_guard:
            reasons.append(
                f"nodes {count_before}->{count_after} "
                f"({(count_before - count_after) / count_before:.1%} removed)"
            )
        if char_guard:
            reasons.append(f"char_loss_ratio={char_loss_ratio:.4f}")
        logger.warning(
            "toc_strip_skipped: %s — over-strip guard fired: %s",
            doc_name,
            "; ".join(reasons),
        )
        TOC_STRIP_SKIPPED.inc()
        return nodes
    return candidate


def _looks_like_toc_page(block_text: str) -> bool:
    """Return True if text looks like a table-of-contents page (dot-leader lines)."""
    text_lines = block_text.splitlines()
    if len(text_lines) < 3:
        return False
    matches = sum(1 for ln in text_lines if _TOC_DOT_LEADER_RE.search(ln))
    return (matches / len(text_lines)) > 0.40
