"""RAG helpers: LLM call + tree-search pipeline."""

import asyncio
import json
import logging
import re
import time
import unicodedata

from .cache import get_doc
from .config import settings
from .converters import normalize_dashes
from .metrics import (
    LLM_CALLS,
    LLM_DURATION,
    RAG_DURATION,
    RAG_SEARCHES,
)

logger = logging.getLogger(__name__)


_FILTER_MODEL = settings.llm_filter_model
_SEARCH_MODEL = settings.llm_search_model
_ANSWER_MODEL = settings.llm_model
_SEARCH_CONCURRENCY = settings.llm_search_concurrency


async def _llm(prompt: str, model: str | None = None) -> str:
    """Call the configured OpenAI-compatible model."""
    LLM_CALLS.inc()
    start = time.monotonic()
    try:
        from .client import get_openai_client

        client = get_openai_client()
        # The litellm ingestion path requires an ``azure/<deployment>`` prefix on
        # model names, but the OpenAI/Azure SDK used here treats ``model`` as the
        # bare Azure deployment name (it becomes the .../deployments/<model>/...
        # URL segment). A leftover ``azure/`` prefix yields a bogus path segment
        # and a 404 "Resource not found". Strip it so a single PAGEINDEX_*_MODEL
        # value works for both paths.
        resolved_model = model or _ANSWER_MODEL
        if resolved_model.startswith("azure/"):
            resolved_model = resolved_model[len("azure/") :]
        r = await client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return r.choices[0].message.content.strip()
    finally:
        LLM_DURATION.observe(time.monotonic() - start)


async def _prefilter_docs(
    query: str,
    doc_summaries: list[dict],
) -> list[str]:
    """Use a fast LLM call to select which documents are worth searching.

    Returns list of doc_ids that are potentially relevant.
    """
    if len(doc_summaries) <= 1:
        return [d["doc_id"] for d in doc_summaries]

    doc_lines = "\n".join(
        f"- doc_id: {d['doc_id']} | name: {d['doc_name']}"
        + (f" | description: {d['doc_description']}" if d.get("doc_description") else "")
        for d in doc_summaries
    )

    prompt = (
        "You are a document relevance filter. Given a user query and a list of "
        "documents (with name and optional description), return ONLY the doc_ids "
        "whose content could plausibly help answer the query.\n\n"
        "Be inclusive — if there's any reasonable chance a document is relevant, "
        "include it. But exclude obviously unrelated documents.\n\n"
        "Match names flexibly: partial names, abbreviations, or surname-only "
        "queries should match full names in document titles.\n\n"
        f"Query: {query}\n\n"
        f"Documents:\n{doc_lines}\n\n"
        'Reply ONLY in JSON: {"relevant_doc_ids": ["id1", "id2"]}'
    )

    t0 = time.monotonic()
    raw = await _llm(prompt, model=_FILTER_MODEL)
    logger.info("RAG TIMING: pre-filter LLM call = %.3fs", time.monotonic() - t0)

    clean = re.sub(r"^```[a-z]*\n?", "", raw.strip())
    clean = re.sub(r"\n?```$", "", clean).strip()

    try:
        parsed = json.loads(clean)
        ids = parsed.get("relevant_doc_ids", [])
        logger.info("RAG pre-filter: %d/%d docs selected: %s", len(ids), len(doc_summaries), ids)
        return ids
    except Exception as e:
        logger.error("RAG pre-filter: failed to parse response, using all docs: %s", e)
        return [d["doc_id"] for d in doc_summaries]


def _strip_text(nodes: list) -> list:
    """Return tree copy without 'text' fields to reduce prompt token usage."""
    result = []
    for n in nodes:
        copy = {k: v for k, v in n.items() if k != "text"}
        if copy.get("nodes"):
            copy["nodes"] = _strip_text(copy["nodes"])
        result.append(copy)
    return result


def _build_node_map(nodes: list, nm: dict) -> None:
    """Recursively flatten tree into {node_id: node} dict."""
    for n in nodes:
        if "node_id" in n:
            nm[n["node_id"]] = n
        if n.get("nodes"):
            _build_node_map(n["nodes"], nm)


async def _rag(query: str, doc_ids: list[str]) -> str:
    """
    Run PageIndex tree-search + answer-generation pipeline.
    doc_ids: list of doc_id strings as stored in MinIO processed/ prefix.
    """
    query = normalize_dashes(query)
    RAG_SEARCHES.inc()
    start = time.monotonic()
    try:
        return await _rag_inner(query, doc_ids)
    finally:
        RAG_DURATION.observe(time.monotonic() - start)


async def _search_one_doc(
    query: str,
    doc_id: str,
    data: dict,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str, str] | None:
    """Search a single document for relevant nodes. Returns (doc_id, name, text) or None."""
    async with semaphore:
        tree = data.get("structure", [])
        name = data.get("doc_name", data.get("filename", doc_id))

        # FLAT-05-C1: flat-doc adapter. A doc that carries a content_class (flat)
        # and has no usable structure[] tree is served from its verbalized flat
        # content (row_records / role-typed block text), BYPASSING the LLM
        # tree-node selection below. A normal tree doc (non-empty structure[])
        # falls through to the unchanged LLM node-selection path. This is a
        # retrieval surface, not an accuracy claim (HR1).
        if data.get("content_class") and not tree:
            text = _flat_search_text(data)
            if text:
                logger.info(
                    "RAG: doc %s (%s) served via flat adapter — %d chars", doc_id, name, len(text)
                )
                return (doc_id, name, text)
            logger.warning("RAG: flat doc %s — no verbalized content to serve", doc_id)
            return None

        tree_slim = _strip_text(tree)

        nm: dict = {}
        _build_node_map(tree, nm)
        logger.info("RAG: searching doc %s (%s) — %d nodes", doc_id, name, len(nm))

        doc_desc = data.get("doc_description", "")
        desc_line = f"\nDocument description: {doc_desc}" if doc_desc else ""

        search_prompt = (
            "You are given a question and a document tree.\n"
            "Each node has a node_id, title, and summary.\n"
            "Find all node_ids whose content likely answers the question.\n"
            "Match names flexibly: partial names, abbreviations, or surname-only "
            "queries should match full names.\n"
            "Select only the most relevant nodes — do NOT select every node in the document.\n\n"
            f"Question: {query}\n"
            f"Document: {name}{desc_line}\n"
            f"Tree:\n{json.dumps(tree_slim, indent=2)}\n\n"
            'Reply ONLY in JSON: {"thinking": "<reasoning>", "node_list": ["id1", "id2"]}'
        )

        llm_t0 = time.monotonic()
        raw = await _llm(search_prompt, model=_SEARCH_MODEL)
        logger.info("RAG TIMING: LLM search(%s) = %.3fs", doc_id, time.monotonic() - llm_t0)
        logger.debug("RAG: LLM raw response for doc %s: %s", doc_id, raw[:500])

        clean = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        clean = re.sub(r"\n?```$", "", clean).strip()

        try:
            parsed = json.loads(clean)
            ids = parsed.get("node_list", [])
            thinking = parsed.get("thinking", "")
            logger.info("RAG: doc %s — LLM selected %d node(s): %s", doc_id, len(ids), ids)
            logger.info("RAG: doc %s — LLM reasoning: %s", doc_id, thinking[:300])
        except Exception as e:
            ids = []
            logger.error(
                "RAG: failed to parse LLM response for doc %s: %s — raw: %s", doc_id, e, clean[:300]
            )

        matched = [i for i in ids if i in nm and "text" in nm[i]]
        missed = [i for i in ids if i not in nm]
        if missed:
            logger.warning(
                "RAG: doc %s — %d node_id(s) from LLM not found in tree: %s",
                doc_id,
                len(missed),
                missed,
            )

        text = "\n\n".join(nm[i]["text"] for i in matched)
        if text:
            logger.info("RAG: doc %s — collected %d chars of context", doc_id, len(text))
            return (doc_id, name, text)
        logger.warning(
            "RAG: doc %s — no text extracted (matched=%d, missed=%d)",
            doc_id,
            len(matched),
            len(missed),
        )
        return None


async def _rag_inner(query: str, doc_ids: list[str]) -> str:
    context_parts: list[str] = []
    matched_docs: list[tuple[str, str]] = []
    logger.info("RAG search starting: query=%r across %d doc(s)", query[:100], len(doc_ids))
    rag_t0 = time.monotonic()

    # --- Phase 1: Load all documents ---
    phase1_t0 = time.monotonic()
    doc_data: dict[str, dict] = {}  # doc_id -> data
    for doc_id in doc_ids:
        t = time.monotonic()
        try:
            data = get_doc(doc_id)
        except ValueError:
            logger.warning("RAG: skipping missing doc %s", doc_id)
            continue
        logger.info("RAG TIMING: load_doc(%s) = %.3fs", doc_id, time.monotonic() - t)
        doc_data[doc_id] = data
    logger.info(
        "RAG TIMING: Phase 1 (load %d docs) = %.3fs", len(doc_data), time.monotonic() - phase1_t0
    )

    # --- Phase 1.5: Pre-filter — pick only relevant docs ---
    prefilter_t0 = time.monotonic()
    doc_summaries = [
        {
            "doc_id": did,
            "doc_name": d.get("doc_name", d.get("filename", did)),
            "doc_description": d.get("doc_description", ""),
        }
        for did, d in doc_data.items()
    ]
    relevant_ids = await _prefilter_docs(query, doc_summaries)
    # Only keep docs the pre-filter selected (fall back to all if none matched)
    filtered = {did: doc_data[did] for did in relevant_ids if did in doc_data}
    if not filtered:
        logger.warning(
            "RAG pre-filter returned no matches, falling back to all %d docs", len(doc_data)
        )
        filtered = doc_data
    logger.info(
        "RAG TIMING: Phase 1.5 (pre-filter %d -> %d docs) = %.3fs",
        len(doc_data),
        len(filtered),
        time.monotonic() - prefilter_t0,
    )

    # --- Phase 2: Parallel LLM search across filtered docs ---
    phase2_t0 = time.monotonic()
    semaphore = asyncio.Semaphore(_SEARCH_CONCURRENCY)
    tasks = [_search_one_doc(query, did, data, semaphore) for did, data in filtered.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            logger.error("RAG: search task failed: %s", result)
            continue
        if result is not None:
            doc_id, name, text = result
            context_parts.append(f"=== {name} ===\n{text}")
            matched_docs.append((doc_id, name))
    logger.info(
        "RAG TIMING: Phase 2 (parallel LLM search across %d docs) = %.3fs",
        len(filtered),
        time.monotonic() - phase2_t0,
    )

    if not context_parts:
        logger.warning(
            "RAG: no relevant content found across %d doc(s) for query=%r",
            len(doc_ids),
            query[:100],
        )
        return json.dumps(
            {"query": query, "sources": [], "content": "No relevant content found for the query."}
        )

    logger.info(
        "RAG: returning %d context part(s) (%d total chars) from %d source(s)",
        len(context_parts),
        sum(len(p) for p in context_parts),
        len(matched_docs),
    )

    # Return raw context + source metadata — let the calling agent synthesize the answer
    result = json.dumps(
        {
            "query": query,
            "sources": [{"doc_id": did, "doc_name": name} for did, name in matched_docs],
            "content": "\n\n".join(context_parts),
        }
    )
    logger.info("RAG TIMING: Total _rag_inner = %.3fs", time.monotonic() - rag_t0)
    return result


class LowQualityTreeError(Exception):
    """Raised when validate_tree rejects a tree (HR5 / WORKER-01-C2).

    Carries .reason ('node_count<3' | 'depth<2' | 'garbling') so the worker can
    surface status=error reason=low_quality_tree without persisting anything."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"low_quality_tree: {reason}")


def _tree_node_count(nodes: list) -> int:
    total = 0
    for n in nodes:
        total += 1
        total += _tree_node_count(n.get("nodes") or [])
    return total


def _tree_depth(nodes: list) -> int:
    if not nodes:
        return 0
    best = 1
    for n in nodes:
        children = n.get("nodes") or []
        if children:
            best = max(best, 1 + _tree_depth(children))
    return best


def _tree_is_garbled(nodes: list) -> bool:
    parts: list[str] = []

    def _walk(ns: list) -> None:
        for n in ns:
            parts.append(str(n.get("title", "")))
            parts.append(str(n.get("text", "")))
            _walk(n.get("nodes") or [])

    _walk(nodes)
    blob = "".join(parts)
    if not blob.strip():
        return True
    if "\x00" in blob or "\ufffd" in blob:
        return True
    bad = sum(1 for c in blob if ord(c) < 32 and c not in "\n\r\t")
    return (bad / len(blob)) > 0.05


def validate_tree(structure: list) -> tuple[bool, str]:
    """Gate a PageIndex tree before persistence (HR5 / WORKER-01-C2).

    Returns (ok, reason); reason is '' when ok. Fails (priority order) on
    node_count < 3, depth < 2, or garbling (null/replacement bytes or a high
    ratio of control characters — the validated German-insurance failure mode)."""
    if _tree_node_count(structure) < 3:
        return False, "node_count<3"
    if _tree_depth(structure) < 2:
        return False, "depth<2"
    if _tree_is_garbled(structure):
        return False, "garbling"
    return True, ""


# ── FLAT-01: deterministic flat-document classifier + block extractor ──────────
# RFC-004 Amendment 1 (D1'/D2'/D3'): a clean-text-layer document with no heading
# hierarchy is a SUCCESS, not a low_quality_tree error. This classifier owns the
# DETERMINISTIC route (VLM stays disabled). It is pure and in-process: it operates
# only on the converter's markdown string and is independent of validate_tree
# (HR5) — it never calls the quality gate, the LLM, MinIO, Redis, or a VLM.

_FLAT_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
# A numbered clause: '1', '1.1', '2.1.3', optionally with a trailing dot/paren and
# an optional title on the same line (e.g. '1.1 Geltungsbereich').
_FLAT_NUMBERED_RE = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?(?:\s+\S.*)?$")


def _flat_split_pipe_row(line: str) -> list[str]:
    """Split a markdown table row into trimmed cells (outer pipes stripped)."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _flat_is_pipe_row(line: str) -> bool:
    return "|" in line and line.strip() != ""


def _flat_is_separator_row(line: str) -> bool:
    """A markdown table header/body separator like '| --- | :--: |'."""
    cells = _flat_split_pipe_row(line)
    if not cells:
        return False
    # Require an actual pipe: a pipe-less thematic break like '---' splits into a
    # single cell that would otherwise pass the dash/colon check and be misread as
    # a table separator (spurious flat_table classification).
    return "|" in line and all(c != "" and set(c) <= set("-: ") and "-" in c for c in cells)


def _flat_verbalize_rows(headers: list[str], data_rows: list[list[str]]) -> list[str]:
    """FLAT-01-C2 / Amendment 1 D2': verbalize each data row as
    'Header: Value; Header2: Value2; ...' with the column headers repeated on
    EVERY row (the retrieval-optimal form)."""
    records: list[str] = []
    for row in data_rows:
        pairs = []
        for i, val in enumerate(row):
            header = headers[i] if i < len(headers) else f"col{i + 1}"
            pairs.append(f"{header}: {val}")
        records.append("; ".join(pairs))
    return records


def _flat_parse_table(lines: list[str], start: int) -> tuple[dict, int]:
    """Parse a markdown table beginning at `start` (a header row followed by a
    separator). Returns (table_block, next_index)."""
    header = _flat_split_pipe_row(lines[start])
    i = start + 2  # skip header + separator
    data_rows: list[list[str]] = []
    while i < len(lines) and _flat_is_pipe_row(lines[i]) and not _flat_is_separator_row(lines[i]):
        data_rows.append(_flat_split_pipe_row(lines[i]))
        i += 1
    block = {
        "role": "table",
        "headers": header,
        "rows": [header, *data_rows],  # structured row matrix
        "row_records": _flat_verbalize_rows(header, data_rows),  # verbalized form
    }
    return block, i


# --- Fix 1: oversized-leaf tail-blob splitter -------------------------------
# Ordinal heading markers (Latin §/Article/Section + Arabic (ال)مادة). REDESIGN:
# markers are matched INLINE (no line anchor) because Docling routinely demotes
# articles after the first to inline prose, so the real "Article (9)…(N)" markers
# in a tail-blob sit mid-line, not at column 0. To stay safe against the inline
# false-positive class (cross-references like "the preceding Article 5"), we (a)
# capture each marker's ordinal NUMBER and (b) keep only the longest STRICTLY
# INCREASING run of those numbers in document order — a real heading sequence is
# monotone 1,2,3,…, while back/forward cross-refs break monotonicity and are
# dropped. Matching runs on an NFKC-folded copy (presentation-form Arabic ﺍﳌـﺎﺩﺓ
# and Latin ligatures normalise to base letters) with an index map back to the
# ORIGINAL text, so every slice is byte-exact on the original (RTL-safe, never
# reordered, never mutated).
_OVERSIZED_ORDINAL_RE = re.compile(
    r"(?:"
    r"§\s*\(?\s*(?P<sec>\d+)"  # § 12 / § (12)
    r"|Art(?:icle|\.)?\s+\(?\s*(?P<art>\d+)"  # Article 9 / Art. 9 / Article (9)
    r"|Section\s+\(?\s*(?P<s>\d+)"  # Section 4 / Section (4)
    r"|(?:ال)?مادة\s*\(?\s*(?P<mada>[\d٠-٩]+)"  # (ال)مادة (5) / المادة ٥
    r")"
)
# Characters dropped before NFKC matching: tatweel/kashida (U+0640) which splits
# Arabic presentation-form glyphs, plus zero-width and bidi control marks that the
# regex must see through. Slicing still uses ORIGINAL indices, so these survive
# untouched in the stored text.
_FOLD_DROP_CHARS = frozenset(
    "ـ"  # ARABIC TATWEEL
    "​‌‍‎‏"  # ZWSP, ZWNJ, ZWJ, LRM, RLM
    "‪‫‬‭‮"  # bidi embeddings/overrides
    "﻿"  # BOM / ZWNBSP
)
_ARABIC_INDIC = {ord(d): ord(a) for d, a in zip("٠١٢٣٤٥٦٧٨٩", "0123456789")}

# Fallback marker for leaves the ordinal path abandons (too few / non-monotonic
# مادة markers — e.g. an RTL reading-order scramble from Docling). فقرة
# ("paragraph") is an un-numbered noun, so there is no ordinal to guard
# monotonicity with; _split_on_paragraph_markers compensates with a minimum
# inter-segment gap and an all-segments-must-shrink acceptance check instead.
_PARAGRAPH_FALLBACK_RE = re.compile(r"(?:ال)?فقرة\b")

# Dotted-leader ToC entries ("Title ......... 12"), used by
# _looks_like_frontmatter_toc to recognise cover/bibliography/table-of-contents
# blocks that should be accepted as-is rather than force-split.
_DOTTED_LEADER_RE = re.compile(r"[.․…]{4,}")


def _fold_with_index_map(text: str) -> tuple[str, list[int]]:
    """NFKC-fold ``text`` for marker matching, returning the folded string and a
    parallel list mapping each folded-char position back to its ORIGINAL index.

    Folding is per-character (compatibility decomposition is per-codepoint for the
    presentation forms and ligatures we care about), so a 1→N expansion maps every
    output char to the single source index. Tatweel/zero-width/bidi marks are
    dropped. Callers slice the original text at the mapped indices — never the
    folded copy — so stored content is byte-identical to the input."""
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


def _ordinal_value(m: "re.Match[str]") -> int:
    """The integer ordinal captured by whichever marker alternative matched."""
    digits = m.group("art") or m.group("sec") or m.group("s") or m.group("mada") or ""
    return int(digits.translate(_ARABIC_INDIC))


def _longest_increasing_run(values: list[int]) -> list[int]:
    """Indices (into ``values``) of a longest STRICTLY-increasing subsequence,
    preserving document order. O(n²) — n is the marker count per blob (≲ a few
    hundred). Ties pick the earliest extension, so heading occurrences (which come
    before their later cross-references) win over duplicates."""
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
    blocks (dotted-leader ToC entries, near-zero ordinal density, a bibliographic
    Latin-script run) that should be accepted as-is rather than force-split.
    Fragmenting a bibliography on paragraph/article markers produces meaningless
    node boundaries. Deliberately narrow: a genuine article-dense Arabic ToC still
    has high ordinal density and is NOT flagged."""
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
    one per entry in ``starts`` (original-text offsets). Shared by the ordinal
    split path and the فقرة fallback path."""
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
    """Fallback for leaves the ordinal path gave up on. Splits on the un-numbered
    noun (ال)?فقرة instead of مادة/Article — there is no ordinal, so no LIS guard
    applies. Dense inline references ("فقرة ٢ من المادة …") are collapsed by a
    minimum inter-segment-chars floor, and the split is accepted only if it
    actually resolves the oversize (every resulting segment < max_chars);
    otherwise the leaf is left untouched rather than half-split."""
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


def split_oversized_leaf_nodes(
    structure: list, max_chars: int = 50000, min_segments: int = 3
) -> list:
    """Fix 1: bounded, deterministic, no-LLM splitter for tail-blob hierarchy
    collapse (REDESIGNED for inline + presentation-form markers).

    The vendored tree builder slices each heading node's ``text`` from one heading
    to the next regardless of depth, so when (e.g. Arabic legal) headings fail to
    level, the last surviving heading swallows the whole document tail into a
    single oversized leaf (Penal Code Art.(9)=236k, Human-Rights=320k, مرسوم
    33=114k). This walks an already-built ``structure`` and, for any LEAF whose
    ``text`` exceeds ``max_chars``, splits it on internal ordinal markers.

    Robustness over the prior line-anchored version: markers are matched inline on
    an NFKC-folded copy (so Latin paren forms ``Article (9)`` and presentation-form
    Arabic both match), and only the longest strictly-increasing ordinal run is
    used as split points — rejecting cross-reference false positives. A blob is
    split only when that run has ≥ ``min_segments`` headings.

    Slicing is byte-exact on the ORIGINAL text via the fold index map (RTL-safe,
    order-preserving). Structure/retrieval fix, never an accuracy claim (HR1); runs
    before ``validate_tree`` and persists nothing itself (HR5); stdlib only (HR4).
    Mutates in place and returns ``structure``. Idempotent: child segments fall
    under ``max_chars`` so a second pass is a no-op."""
    for node in structure or []:
        if not isinstance(node, dict):
            continue
        children = node.get("nodes")
        if children:
            # Parent node: recurse, leave its own text untouched.
            split_oversized_leaf_nodes(children, max_chars, min_segments)
            continue

        text = node.get("text") or ""
        if len(text) <= max_chars:
            continue

        folded, idx_map = _fold_with_index_map(text)
        all_matches = list(_OVERSIZED_ORDINAL_RE.finditer(folded))

        # Cover/bibliography/ToC blocks (dotted leaders, ~no ordinal markers):
        # accept as-is rather than force-splitting a bibliography on فقرة.
        if _looks_like_frontmatter_toc(text, all_matches):
            continue

        if len(all_matches) < min_segments:
            if _split_on_paragraph_markers(node, text, max_chars, min_segments):
                split_oversized_leaf_nodes(node["nodes"], max_chars, min_segments)
            continue

        # Keep only the longest strictly-increasing ordinal run (drops cross-refs).
        values = [_ordinal_value(m) for m in all_matches]
        keep_idx = _longest_increasing_run(values)
        if len(keep_idx) < min_segments:
            # مادة/Article markers exist but don't form a long enough increasing
            # run (e.g. RTL reading-order scramble) — fall back to فقرة.
            if _split_on_paragraph_markers(node, text, max_chars, min_segments):
                split_oversized_leaf_nodes(node["nodes"], max_chars, min_segments)
            continue
        # Map kept markers back to ORIGINAL text start offsets, in order.
        starts = [idx_map[all_matches[k].start()] for k in keep_idx]

        _apply_split(node, text, starts)
        # Recurse into the new children: a single article that is itself oversized
        # (sub-clauses, or a gap whose inner markers were not part of the top-level
        # increasing run) gets a second split pass. Terminates because each pass
        # strictly shrinks segments.
        split_oversized_leaf_nodes(node["nodes"], max_chars, min_segments)

    return structure


# --- Fix 2: table fidelity in the flat path ---------------------------------
# Arabic-script ranges (incl. presentation forms) for the RTL ratio heuristic.
_ARABIC_SCRIPT_RE = re.compile(
    r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]"
)
# A header/cell that is date/numeric-like: starts with a (Western or Arabic-Indic)
# digit and contains only digits + common date/number separators (no text label).
_NUMERIC_DATE_RE = re.compile(r"^[\d٠-٩][\d٠-٩\s/\-.:,]*$")


def _is_numeric_or_date(cell: object) -> bool:
    s = str(cell).strip()
    if s == "":
        return False
    return bool(_NUMERIC_DATE_RE.match(s))


def table_is_rtl(block: dict) -> bool:
    """Fix 2b: True when the table block is right-to-left.

    Docling cell-bbox metadata is NOT available on these flat blocks, so this
    uses a script-ratio heuristic: Arabic-script character ratio across the
    block's headers + cells > 0.3 ⇒ RTL. Pure, no external dep.
    """
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


def _is_continuation_table(anchor: dict, cont: dict) -> bool:
    """A later table block continues `anchor` when it has the same number of data
    rows AND all of its headers are date/numeric-like (no row-label column)."""
    a_data = (anchor.get("rows") or [])[1:]
    c_data = (cont.get("rows") or [])[1:]
    if len(a_data) != len(c_data) or not c_data:
        return False
    c_headers = cont.get("headers") or []
    if not c_headers:
        return False
    return all(_is_numeric_or_date(h) for h in c_headers)


def _merge_continuation_table(anchor: dict, cont: dict) -> dict:
    """Left-key on the anchor's row-label column and concatenate the
    continuation's data columns onto each row. For an RTL anchor the continuation
    columns are inserted right after the label column (prepended ahead of the
    anchor's own series) so the series reads right-to-left consistently while the
    row label still keys the join. Regenerates row_records via the existing
    verbalizer. Pure, no LLM, no AGPL."""
    a_headers = list(anchor.get("headers") or [])
    c_headers = list(cont.get("headers") or [])
    a_data = (anchor.get("rows") or [])[1:]
    c_data = (cont.get("rows") or [])[1:]

    if table_is_rtl(anchor):
        label_idx = [k for k, h in enumerate(a_headers) if not _is_numeric_or_date(h)]
        date_idx = [k for k, h in enumerate(a_headers) if _is_numeric_or_date(h)]
        merged_headers = (
            [a_headers[k] for k in label_idx]
            + c_headers
            + [a_headers[k] for k in date_idx]
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
    """Fix 2a: merge wide tables paginated across pages back together.

    A wide table split across PDF pages arrives as several consecutive
    ``role:'table'`` blocks; slices 2..N carry date/numeric-only headers and have
    lost the row-label column. This walks the blocks and, for each table that is
    followed by one or more continuation slices, left-keys on the anchor's
    row-label column and concatenates the continuation data columns (RTL-aware via
    `table_is_rtl`). Non-continuation tables pass through untouched. Pure, no LLM,
    no AGPL."""
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
            j < n
            and blocks[j].get("role") == "table"
            and _is_continuation_table(anchor, blocks[j])
        ):
            anchor = _merge_continuation_table(anchor, blocks[j])
            j += 1
        result.append(anchor)
        i = j
    return result


def flag_empty_cells(block: dict) -> dict:
    """Fix 2c: annotate (never drop) a table block with an empty-cell quality
    signal: ``block['quality'] = {'empty_cell_ratio': float, 'suspected_miss':
    bool}`` where suspected_miss is True when an entire data row or column is
    empty (a TableFormer miss signal). Returns the block."""
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


# Complexity grandfathered (flat-doc router, FLAT-01); see pyproject [tool.ruff].
def route_and_extract_flat(md: str) -> tuple[str, list[dict]]:  # noqa: PLR0915
    """FLAT-01-C1/C2/C3: classify a flat (no-hierarchy) markdown document and
    extract role-typed blocks.

    Returns (content_class, blocks) where content_class is one of
    'flat_table', 'flat_kv', 'flat_prose', 'flat_mixed'. The decision uses only
    deterministic markdown-text signals:
      * a markdown grid/table region            -> table signal
      * numbered-clause lines ('1', '1.1', ...) -> kv signal
      * running paragraphs                       -> prose signal
    A single signal names the class; more than one co-present signal -> flat_mixed.

    Every block carries a role in {title, prose, kv, table}. Pure / in-process:
    no validate_tree, no LLM, no MinIO/Redis/VLM call (HR5; not an accuracy claim,
    HR1)."""
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

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped == "":
            flush_prose()
            i += 1
            continue

        # Table region: a pipe row immediately followed by a separator row.
        if _flat_is_pipe_row(line) and i + 1 < n and _flat_is_separator_row(lines[i + 1]):
            flush_prose()
            block, i = _flat_parse_table(lines, i)
            blocks.append(block)
            signals.add("table")
            continue

        # Heading -> title block (does not by itself decide the content class).
        m_head = _FLAT_HEADING_RE.match(line)
        if m_head:
            flush_prose()
            blocks.append({"role": "title", "text": m_head.group(1).strip()})
            i += 1
            continue

        # Numbered-clause line -> kv block.
        if _FLAT_NUMBERED_RE.match(line):
            flush_prose()
            blocks.append({"role": "kv", "text": stripped})
            signals.add("kv")
            i += 1
            continue

        # Otherwise running prose.
        prose_buf.append(stripped)
        i += 1

    flush_prose()

    # Fix 2a/2c post-pass: stitch wide paginated tables back into one and annotate
    # empty-cell quality. Pure / in-process; the "table" signal stays in `signals`
    # so the content_class decision below is unaffected (HR5; not an HR1 claim).
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


def _flat_search_text(data: dict) -> str:
    """FLAT-05-C1 helper: render a flat doc's verbalized content as a single
    retrieval string — table row_records plus role-typed block text. Pure."""
    parts: list[str] = []
    for block in data.get("blocks", []) or []:
        if block.get("role") == "table":
            parts.extend(block.get("row_records", []) or [])
        else:
            txt = block.get("text")
            if txt:
                parts.append(txt)
    # Tolerate a top-level row_records list if a caller pre-flattened it.
    for rec in data.get("row_records", []) or []:
        if rec not in parts:
            parts.append(rec)
    return "\n".join(parts)


def flat_doc_view(data: dict) -> dict | None:
    """FLAT-05-C2: build the get_document / get_document_structure response shape
    for a flat doc — exposing content_class and its blocks/row_records instead of
    an empty structure tree. Returns None for a non-flat (tree) doc so the
    transport keeps the existing node-map / structure shape (boundary). This is a
    retrieval surface, not an accuracy claim (HR1)."""
    content_class = data.get("content_class")
    if not content_class:
        return None

    blocks = data.get("blocks", []) or []
    row_records: list[str] = []
    for block in blocks:
        if block.get("role") == "table":
            row_records.extend(block.get("row_records", []) or [])
    for rec in data.get("row_records", []) or []:
        if rec not in row_records:
            row_records.append(rec)

    return {
        "doc_name": data.get("doc_name", data.get("filename", "")),
        "content_class": content_class,
        "blocks": blocks,
        "row_records": row_records,
        "structure": [],
    }
