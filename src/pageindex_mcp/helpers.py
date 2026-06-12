"""RAG helpers: LLM call + tree-search pipeline."""

import asyncio
import json
import logging
import re
import time

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
        r = await client.chat.completions.create(
            model=model or _ANSWER_MODEL,
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
