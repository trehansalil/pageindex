"""Tree manipulation: oversized-leaf splitting and table-node segmentation."""

from __future__ import annotations

import logging
import os
import re
import unicodedata

from ..config import pipeline_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fix 1: oversized-leaf tail-blob splitter
# ---------------------------------------------------------------------------
_OVERSIZED_ORDINAL_RE = re.compile(
    r"(?:"
    r"§\s*\(?\s*(?P<sec>\d+(?:\.\d+)?)"  # § 12 / § (12) / § 12.1
    r"|Art(?:icle|\.)?\s+\(?\s*(?P<art>\d+(?:\.\d+)?)"  # Article 9 / Art. 9 / Article (9)
    r"|Section\s+\(?\s*(?P<s>\d+(?:\.\d+)?)"  # Section 4 / Section (4) / Section 4.2
    r"|Schedule\s+\(?\s*(?P<sched>\d+(?:\.\d+)?)"  # RFC-015 D5b: Schedule 3 / Schedule (3)
    r"|(?:ال)?مادة\s*\(?\s*(?P<mada>[\d٠-٩]+(?:[.٫][\d٠-٩]+)?)"  # (ال)مادة (5) / المادة ٥
    # RFC-024 D3: MOU/decree markers (Clause/Part/Annex + بند/باب)
    r"|Clause\s+\(?\s*(?P<clause>\d+(?:\.\d+)?)"  # Clause 4 / Clause (4)
    r"|Part\s+\(?\s*(?P<part>(?:[IVX]+|\d+)(?:\.\d+)?)"  # Part IV / Part 4
    r"|بند\s*\(?\s*(?P<band>[\d٠-٩]+(?:[.٫][\d٠-٩]+)?)"  # بند (5) / بند ٥
    r"|باب\s*\(?\s*(?P<bab>[\d٠-٩]+(?:[.٫][\d٠-٩]+)?)"  # باب (5) / باب ٥
    r"|Annex\s+\(?\s*(?P<annex>[A-Z]|\d+(?:\.\d+)?)"  # Annex A / Annex 4
    # RFC-028 D7: standalone Roman-numeral sub-clause markers ("I. ", "II. ").
    r"|(?P<roman>[IVX]+)\.\s"
    r")",
    re.IGNORECASE,
)
_FOLD_DROP_CHARS = frozenset(
    "ـ"  # ARABIC TATWEEL
    "​‌‍‎‏"  # ZWSP, ZWNJ, ZWJ, LRM, RLM
    "‪‫‬‭‮"  # bidi embeddings/overrides
    "﻿"  # BOM / ZWNBSP
)
_ARABIC_INDIC = {ord(d): ord(a) for d, a in zip("٠١٢٣٤٥٦٧٨٩", "0123456789", strict=True)}

_PARAGRAPH_FALLBACK_RE = re.compile(r"(?:ال)?فقرة\b")

_GENERIC_NUMBERED_RE = re.compile(
    r"^\s*(?P<gnum>\d+(?:\.\d+)*(?:\.[a-z])?)\s*[.\):]\s",
    re.MULTILINE,
)

_DOTTED_LEADER_RE = re.compile(r"[.․…]{4,}")

_PREAMBLE_MIN_CHARS = 50

# Fix 2: table fidelity — Arabic-script ranges for RTL ratio heuristic.
_ARABIC_SCRIPT_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_NUMERIC_DATE_RE = re.compile(r"^[\d٠-٩][\d٠-٩\s/\-.:,]*$")

# Zone-5: table-segmentation constants sourced from pipeline_config.
_RFC029_TABLE_SEGMENT_CHAR_THRESHOLD: int = pipeline_config.rfc029_table_segment_char_threshold
_RFC029_TABLE_SEGMENT_MIN_ROWS: int = pipeline_config.rfc029_table_segment_min_rows
_RFC036_SINGLETON_ROW_RATIO_THRESHOLD: float = pipeline_config.rfc036_singleton_row_ratio_threshold
_RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE: int = (
    pipeline_config.rfc029_table_segment_min_rows_landscape
)
_RFC036_SINGLETON_RATIO_LANDSCAPE: float = pipeline_config.rfc036_singleton_ratio_landscape

# Heading regex — imported from flat.py would create a circular dep at module
# load time (flat imports from tree_split); define locally to break the cycle.
_FLAT_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


# ---------------------------------------------------------------------------
# Fold / ordinal helpers
# ---------------------------------------------------------------------------


def _fold_with_index_map(text: str) -> tuple[str, list[int]]:
    """NFKC-fold ``text`` for marker matching, returning the folded string and a
    parallel list mapping each folded-char position back to its ORIGINAL index."""
    folded: list[str] = []
    idx_map: list[int] = []
    for i, ch in enumerate(text):
        if ch in _FOLD_DROP_CHARS:
            continue
        nf = unicodedata.normalize("NFKC", ch)
        for c in nf:
            folded.append(c)
            idx_map.append(i)
    return "".join(folded), idx_map


def _roman_to_int(s: str) -> int:
    """Convert an uppercase Roman numeral (``I``-``XXXIX``) to an int."""
    values = {"I": 1, "V": 5, "X": 10}
    total = 0
    prev = 0
    for ch in reversed(s):
        val = values[ch]
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total


def _ordinal_value(m: re.Match[str]) -> tuple[int, ...]:
    """The ordinal captured by whichever marker alternative matched, as a tuple of
    dotted components compared lexicographically."""
    part = m.group("part")
    if part is not None:
        return tuple(int(p) if p.isdigit() else _roman_to_int(p.upper()) for p in part.split("."))
    roman = m.group("roman")  # RFC-028 D7
    if roman is not None:
        return (_roman_to_int(roman.upper()),)
    annex = m.group("annex")
    if annex is not None:
        try:
            return tuple(int(p) for p in annex.split("."))
        except ValueError:
            return (ord(annex.upper()) - ord("A") + 1,)
    digits = (
        m.group("clause")  # RFC-024 D3
        or m.group("band")  # RFC-024 D3
        or m.group("bab")  # RFC-024 D3
        or m.group("art")
        or m.group("sec")
        or m.group("s")
        or m.group("sched")  # RFC-015 D5b
        or m.group("mada")
        or ""
    )
    digits = digits.translate(_ARABIC_INDIC).replace("٫", ".")
    return tuple(int(part) for part in digits.split("."))


def _longest_increasing_run(values: list[tuple[int, ...]]) -> list[int]:
    """Indices (into ``values``) of a longest STRICTLY-increasing subsequence,
    preserving document order. O(n²)."""
    n = len(values)
    if n == 0:
        return []
    best_len = [1] * n
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            if values[j] < values[i] and best_len[j] + 1 > best_len[i]:
                best_len[i] = best_len[j] + 1
                prev[i] = j
    end = max(range(n), key=lambda k: best_len[k])
    seq: list[int] = []
    while end != -1:
        seq.append(end)
        end = prev[end]
    seq.reverse()
    return seq


def _looks_like_frontmatter_toc(text: str, ordinal_matches: list) -> bool:
    """Conservative all-three-AND gate for cover/bibliography/table-of-contents
    blocks that should be accepted as-is rather than force-split."""
    length = len(text)
    if length == 0:
        return False
    per_1k = length / 1000
    if len(_DOTTED_LEADER_RE.findall(text)) / per_1k < 1.0:
        return False
    if len(ordinal_matches) / per_1k >= 0.1:
        return False
    return re.search(r"[A-Za-z]{20,}", text) is not None


def _apply_split(node: dict, text: str, starts: list[int]) -> None:
    """Rebuild ``node`` into a parent (preamble text) + ordered leaf children,
    one per entry in ``starts`` (original-text offsets)."""
    parent_id = node.get("node_id") or "x"
    new_children: list[dict] = []
    for idx, seg_start in enumerate(starts):
        seg_end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        seg = text[seg_start:seg_end]
        seg_lines = seg.splitlines()
        title = (seg_lines[0].strip() if seg_lines else seg.strip())[:120]
        child: dict = {
            "title": title,
            "text": seg,
            "nodes": [],
            "node_id": f"{parent_id}-s{idx}",
        }
        if "start_index" in node:
            child["start_index"] = node["start_index"]
        if "end_index" in node:
            child["end_index"] = node["end_index"]
        new_children.append(child)
    node["text"] = text[: starts[0]]
    node["nodes"] = new_children


def _split_on_paragraph_markers(
    node: dict,
    text: str,
    max_chars: int,
    min_segments: int,
    min_seg_chars: int = 5000,
) -> bool:
    """Fallback for leaves the ordinal path gave up on. Splits on (ال)?فقرة."""
    folded, idx_map = _fold_with_index_map(text)
    matches = list(_PARAGRAPH_FALLBACK_RE.finditer(folded))
    if len(matches) < min_segments:
        return False

    starts: list[int] = []
    for m in matches:
        orig = idx_map[m.start()]
        if starts and orig - starts[-1] < min_seg_chars:
            continue
        starts.append(orig)
    if len(starts) < 2:
        return False

    for idx, seg_start in enumerate(starts):
        seg_end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        if seg_end - seg_start >= max_chars:
            return False

    _apply_split(node, text, starts)
    return True


def _split_on_blank_line_paragraphs(
    node: dict,
    text: str,
    max_chars: int,
    min_segments: int,
    min_seg_chars: int = 2000,
) -> bool:
    """RFC-024 D3 (Task 2.3): last-resort fallback — splits on blank-line-separated
    paragraph boundaries."""
    matches = list(re.finditer(r"\n[ \t]*\n+", text))
    if not matches:
        return False

    starts = [0]
    for m in matches:
        if m.end() - starts[-1] >= min_seg_chars:
            starts.append(m.end())
    if len(starts) < min_segments:
        return False

    for idx, seg_start in enumerate(starts):
        seg_end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        if seg_end - seg_start >= max_chars:
            return False

    _apply_split(node, text, starts)
    return True


def _split_on_atx_headings(
    node: dict,
    text: str,
    max_chars: int,
    min_segments: int,
) -> bool:
    """Structure-agnostic fallback: splits on ATX-style markdown headings."""
    matches = list(re.finditer(r"^\s{0,3}#{1,6}\s+", text, re.MULTILINE))
    if not matches:
        return False

    starts: list[int] = [m.start() for m in matches if m.start() > 0]
    if len(starts) < 2:
        return False

    for idx, seg_start in enumerate(starts):
        seg_end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        if seg_end - seg_start >= max_chars:
            return False

    _apply_split(node, text, starts)
    return True


def _split_on_generic_numbered_lines(
    node: dict,
    text: str,
    max_chars: int,
    min_segments: int,
    min_seg_chars: int = 5000,
) -> bool:
    """Structure-agnostic fallback: splits on generic numbered lines
    (``1.``, ``1.1``, ``7.10.a)``)."""
    matches = list(_GENERIC_NUMBERED_RE.finditer(text))
    if len(matches) < min_segments:
        return False

    values: list[tuple[int, ...]] = []
    for m in matches:
        raw = m.group("gnum").translate(_ARABIC_INDIC).replace("٫", ".")
        parts = raw.split(".")
        parsed: list[int] = []
        for p in parts:
            if not p:
                continue
            if p.isdigit():
                parsed.append(int(p))
            elif len(p) == 1 and p.isalpha():
                parsed.append(ord(p.lower()) - ord("a") + 1)
            else:
                break
        values.append(tuple(parsed) if parsed else (0,))

    keep_idx = _longest_increasing_run(values)
    if len(keep_idx) < min_segments:
        return False

    starts: list[int] = []
    for k in keep_idx:
        pos = matches[k].start()
        if starts and pos - starts[-1] < min_seg_chars:
            continue
        starts.append(pos)
    if len(starts) < 2:
        return False

    for idx, seg_start in enumerate(starts):
        seg_end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        if seg_end - seg_start >= max_chars:
            return False

    _apply_split(node, text, starts)
    return True


def _synthesize_preamble_node(md_text: str, tree: dict) -> dict:
    """RFC-015 D10: recover body text that precedes a document's first heading."""
    if not md_text or not isinstance(tree, dict):
        return tree

    structure = tree.get("structure")
    if not isinstance(structure, list):
        return tree

    lines = md_text.splitlines()
    first_heading_idx = None
    for i, line in enumerate(lines):
        if _FLAT_HEADING_RE.match(line):
            first_heading_idx = i
            break

    if first_heading_idx is None:
        return tree

    if first_heading_idx == 0:
        return tree

    preamble = "\n".join(lines[:first_heading_idx])
    if len(preamble.strip()) <= _PREAMBLE_MIN_CHARS:
        return tree

    preamble_node: dict = {
        "title": "[Preamble]",
        "text": preamble,
        "nodes": [],
        "node_id": "preamble",
        "start_index": 0,
        "end_index": max(first_heading_idx - 1, 0),
    }
    structure.insert(0, preamble_node)
    return tree


def _has_heading_markers(text: str) -> bool:
    """RFC-015 D5a: lightweight check for any ``_OVERSIZED_ORDINAL_RE`` marker."""
    if not text:
        return False
    folded, _ = _fold_with_index_map(text)
    return _OVERSIZED_ORDINAL_RE.search(folded) is not None


def _blank_line_fallback_enabled(tree_ratio: float) -> bool:
    """RFC-024 D3 (Task 2.3): gate for the blank-line paragraph-boundary fallback."""
    enabled = os.environ.get("LEAF_CONCENTRATION_PARAGRAPH_SPLIT_ENABLED", "true")
    if enabled.strip().lower() in {"false", "0", "no", "off"}:
        return False
    leaf_split_ratio = float(os.environ.get("LEAF_SPLIT_RATIO", "0.30"))
    return tree_ratio > leaf_split_ratio


def prepare_tree(
    structure: list,
    orientation: str | None = None,
) -> list:
    """Single entry point for pre-validation tree transforms.

    Runs split_oversized_leaf_nodes then _segment_table_nodes on *structure*."""
    structure = split_oversized_leaf_nodes(structure)
    structure = _segment_table_nodes(structure, orientation=orientation)
    return structure


def split_oversized_leaf_nodes(
    structure: list,
    max_chars: int = 50000,
    min_segments: int = 3,
    _tree_ratio: float | None = None,
    _tree_total: int | None = None,
) -> list:
    """Fix 1: bounded, deterministic, no-LLM splitter for tail-blob hierarchy
    collapse (REDESIGNED for inline + presentation-form markers)."""
    from .tree_validation import _tree_max_leaf_ratio

    if _tree_ratio is None:
        _, _tree_total, _tree_ratio = _tree_max_leaf_ratio(structure)

    for node in structure or []:
        if not isinstance(node, dict):
            continue
        children = node.get("nodes")
        if children:
            split_oversized_leaf_nodes(children, max_chars, min_segments, _tree_ratio, _tree_total)
            continue

        text = node.get("text") or ""
        if len(text) <= max_chars and not _has_heading_markers(text):
            leaf_share = (
                (len(node.get("title", "")) + len(text)) / _tree_total if _tree_total else 0.0
            )
            if not _blank_line_fallback_enabled(leaf_share):
                continue

        folded, idx_map = _fold_with_index_map(text)
        all_matches = list(_OVERSIZED_ORDINAL_RE.finditer(folded))

        roman_idx = {i for i, m in enumerate(all_matches) if m.group("roman") is not None}
        if 0 < len(roman_idx) < 2:
            all_matches = [m for i, m in enumerate(all_matches) if i not in roman_idx]

        if _looks_like_frontmatter_toc(text, all_matches):
            continue

        if len(all_matches) < min_segments:
            if (
                _split_on_atx_headings(node, text, max_chars, min_segments)
                or _split_on_generic_numbered_lines(node, text, max_chars, min_segments)
                or _split_on_paragraph_markers(node, text, max_chars, min_segments)
                or (
                    _blank_line_fallback_enabled(_tree_ratio)
                    and _split_on_blank_line_paragraphs(node, text, max_chars, min_segments)
                )
            ):
                split_oversized_leaf_nodes(
                    node["nodes"], max_chars, min_segments, _tree_ratio, _tree_total
                )
            continue

        values = [_ordinal_value(m) for m in all_matches]
        keep_idx = _longest_increasing_run(values)
        if len(keep_idx) < min_segments:
            if (
                _split_on_atx_headings(node, text, max_chars, min_segments)
                or _split_on_generic_numbered_lines(node, text, max_chars, min_segments)
                or _split_on_paragraph_markers(node, text, max_chars, min_segments)
                or (
                    _blank_line_fallback_enabled(_tree_ratio)
                    and _split_on_blank_line_paragraphs(node, text, max_chars, min_segments)
                )
            ):
                split_oversized_leaf_nodes(
                    node["nodes"], max_chars, min_segments, _tree_ratio, _tree_total
                )
            continue
        starts = [idx_map[all_matches[k].start()] for k in keep_idx]

        _apply_split(node, text, starts)
        split_oversized_leaf_nodes(node["nodes"], max_chars, min_segments, _tree_ratio, _tree_total)

    return structure


def _segment_table_nodes(structure: list, *, orientation: str | None = None) -> list:  # noqa: C901, PLR0915
    """RFC-029 D7 (Task 5.3, Property 9) — table-aware node segmentation."""
    _SEP_RE = re.compile(r"^\|[\s|:-]+\|$")
    _PIPE_START = "|"

    def _is_pipe_row(line: str) -> bool:
        s = line.strip()
        return s.startswith(_PIPE_START) and s.endswith(_PIPE_START) and len(s) > 1

    def _is_sep_row(line: str) -> bool:
        return bool(_SEP_RE.match(line.strip()))

    def _count_table_data_rows(table_lines: list[str]) -> int:
        count = 0
        past_sep = False
        for ln in table_lines:
            if _is_sep_row(ln):
                past_sep = True
                continue
            if past_sep and _is_pipe_row(ln):
                count += 1
        return count

    def _singleton_row_ratio(table_lines: list[str]) -> float:
        total = 0
        singleton = 0
        past_sep = False
        for ln in table_lines:
            if _is_sep_row(ln):
                past_sep = True
                continue
            if past_sep and _is_pipe_row(ln):
                cells = [c.strip() for c in ln.strip().split("|") if c.strip()]
                total += 1
                if len(cells) <= 1:
                    singleton += 1
        return singleton / total if total else 0.0

    def _extract_header_text(table_lines: list[str]) -> str:
        for ln in table_lines:
            if _is_pipe_row(ln) and not _is_sep_row(ln):
                cells = [c.strip() for c in ln.strip().split("|") if c.strip()]
                return " | ".join(cells[:3]) if cells else ""
        return ""

    if orientation == "landscape":
        _eff_min_rows = _RFC029_TABLE_SEGMENT_MIN_ROWS_LANDSCAPE
        _eff_singleton_ratio = _RFC036_SINGLETON_RATIO_LANDSCAPE
    else:
        _eff_min_rows = _RFC029_TABLE_SEGMENT_MIN_ROWS
        _eff_singleton_ratio = _RFC036_SINGLETON_ROW_RATIO_THRESHOLD

    def _split_node(node: dict) -> None:  # noqa: C901, PLR0915
        text = node.get("text") or ""
        if len(text) <= _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD:
            return

        lines = text.splitlines(keepends=True)
        parent_title = node.get("title") or ""

        table_spans: list[tuple[int, int]] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if _is_pipe_row(line):
                start = i
                while i < len(lines) and (_is_pipe_row(lines[i]) or lines[i].strip() == ""):
                    i += 1
                end = i
                while end > start and lines[end - 1].strip() == "":
                    end -= 1
                table_block = lines[start:end]
                table_block_stripped = [ln.rstrip("\n") for ln in table_block]
                data_rows = _count_table_data_rows(table_block_stripped)
                if (
                    data_rows >= _eff_min_rows
                    and _singleton_row_ratio(table_block_stripped) <= _eff_singleton_ratio
                ):
                    table_spans.append((start, end))
            else:
                i += 1

        if not table_spans:
            return

        children: list[dict] = []
        cursor = 0
        child_idx = 0

        for t_start, t_end in table_spans:
            if cursor < t_start:
                prose_lines = lines[cursor:t_start]
                prose_text = "".join(prose_lines).rstrip()
                if prose_text:
                    children.append(
                        {
                            "title": parent_title if child_idx == 0 else f"{parent_title} (cont.)",
                            "text": prose_text,
                            "nodes": [],
                        }
                    )
                    child_idx += 1

            table_lines_raw = lines[t_start:t_end]
            table_text = "".join(table_lines_raw).rstrip()
            header_candidate = _extract_header_text([ln.rstrip("\n") for ln in table_lines_raw])
            table_title = header_candidate if header_candidate else f"Table: {parent_title}"
            children.append(
                {
                    "title": table_title,
                    "text": table_text,
                    "nodes": [],
                }
            )
            child_idx += 1
            cursor = t_end

        if cursor < len(lines):
            trailing = "".join(lines[cursor:]).rstrip()
            if trailing:
                children.append(
                    {
                        "title": f"{parent_title} (cont.)",
                        "text": trailing,
                        "nodes": [],
                    }
                )

        if len(children) <= 1:
            return

        joined = "\n".join(c["text"] for c in children)
        if joined.replace("\n", "") != text.replace("\n", ""):
            logger.warning(
                "_segment_table_nodes: content-preservation check failed for node %r; "
                "skipping split",
                parent_title,
            )
            return

        parent_id = node.get("node_id", "")
        parent_page = node.get("page")
        for i, child in enumerate(children):
            child["node_id"] = f"{parent_id}_seg{i}" if parent_id else f"seg{i}"
            if parent_page is not None:
                child["page"] = parent_page
        node["nodes"] = children
        node["text"] = ""

    def _walk(nodes: list) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            children = node.get("nodes")
            if children:
                _walk(children)
            else:
                _split_node(node)

    _walk(structure)
    return structure


# ---------------------------------------------------------------------------
# Fix 2: table fidelity helpers
# ---------------------------------------------------------------------------


def _is_numeric_or_date(cell: object) -> bool:
    s = str(cell).strip()
    if s == "":
        return False
    return bool(_NUMERIC_DATE_RE.match(s))


def table_is_rtl(block: dict) -> bool:
    """Fix 2b: True when the table block is right-to-left (script-ratio heuristic)."""
    texts: list[object] = list(block.get("headers") or [])
    for row in block.get("rows") or []:
        texts.extend(row)
    arabic = 0
    total = 0
    for t in texts:
        for ch in str(t):
            if ch.isspace():
                continue
            total += 1
            if _ARABIC_SCRIPT_RE.match(ch):
                arabic += 1
    if total == 0:
        return False
    return (arabic / total) > 0.3
