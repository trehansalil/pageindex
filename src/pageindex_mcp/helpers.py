"""RAG helpers: LLM call + tree-search pipeline."""

import asyncio
import json
import logging
import os
import re
import time

import openai

from .metrics import (
    LLM_CALLS,
    LLM_DURATION,
    RAG_DURATION,
    RAG_SEARCHES,
)
from .storage import load_doc

logger = logging.getLogger(__name__)


_FILTER_MODEL = os.environ.get("PAGEINDEX_FILTER_MODEL", "gpt-4o-mini")
_SEARCH_MODEL = os.environ.get("PAGEINDEX_SEARCH_MODEL", "gpt-4o-mini")
_ANSWER_MODEL = os.environ.get("PAGEINDEX_MODEL", "gpt-4o-2024-11-20")
_SEARCH_CONCURRENCY = int(os.environ.get("PAGEINDEX_SEARCH_CONCURRENCY", "3"))


async def _llm(prompt: str, model: str | None = None) -> str:
    """Call the configured OpenAI-compatible model."""
    LLM_CALLS.inc()
    start = time.monotonic()
    try:
        client = openai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        r = await client.chat.completions.create(
            model=model or _ANSWER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return r.choices[0].message.content.strip()
    finally:
        LLM_DURATION.observe(time.monotonic() - start)


async def _prefilter_docs(
    query: str, doc_summaries: list[dict],
) -> list[str]:
    """Use a fast LLM call to select which documents are worth searching.

    Returns list of doc_ids that are potentially relevant.
    """
    if len(doc_summaries) <= 1:
        return [d["doc_id"] for d in doc_summaries]

    doc_lines = "\n".join(
        f'- doc_id: {d["doc_id"]} | name: {d["doc_name"]}'
        + (f' | description: {d["doc_description"]}' if d.get("doc_description") else "")
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
    RAG_SEARCHES.inc()
    start = time.monotonic()
    try:
        return await _rag_inner(query, doc_ids)
    finally:
        RAG_DURATION.observe(time.monotonic() - start)


async def _search_one_doc(
    query: str, doc_id: str, data: dict, semaphore: asyncio.Semaphore,
) -> tuple[str, str, str] | None:
    """Search a single document for relevant nodes. Returns (doc_id, name, text) or None."""
    async with semaphore:
        tree = data.get("structure", [])
        name = data.get("doc_name", data.get("filename", doc_id))
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
            logger.error("RAG: failed to parse LLM response for doc %s: %s — raw: %s", doc_id, e, clean[:300])

        matched = [i for i in ids if i in nm and "text" in nm[i]]
        missed = [i for i in ids if i not in nm]
        if missed:
            logger.warning("RAG: doc %s — %d node_id(s) from LLM not found in tree: %s", doc_id, len(missed), missed)

        text = "\n\n".join(nm[i]["text"] for i in matched)
        if text:
            logger.info("RAG: doc %s — collected %d chars of context", doc_id, len(text))
            return (doc_id, name, text)
        logger.warning("RAG: doc %s — no text extracted (matched=%d, missed=%d)", doc_id, len(matched), len(missed))
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
            data = load_doc(doc_id)
        except ValueError:
            logger.warning("RAG: skipping missing doc %s", doc_id)
            continue
        logger.info("RAG TIMING: load_doc(%s) = %.3fs", doc_id, time.monotonic() - t)
        doc_data[doc_id] = data
    logger.info("RAG TIMING: Phase 1 (load %d docs) = %.3fs", len(doc_data), time.monotonic() - phase1_t0)

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
        logger.warning("RAG pre-filter returned no matches, falling back to all %d docs", len(doc_data))
        filtered = doc_data
    logger.info(
        "RAG TIMING: Phase 1.5 (pre-filter %d -> %d docs) = %.3fs",
        len(doc_data), len(filtered), time.monotonic() - prefilter_t0,
    )

    # --- Phase 2: Parallel LLM search across filtered docs ---
    phase2_t0 = time.monotonic()
    semaphore = asyncio.Semaphore(_SEARCH_CONCURRENCY)
    tasks = [
        _search_one_doc(query, did, data, semaphore)
        for did, data in filtered.items()
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            logger.error("RAG: search task failed: %s", result)
            continue
        if result is not None:
            doc_id, name, text = result
            context_parts.append(f"=== {name} ===\n{text}")
            matched_docs.append((doc_id, name))
    logger.info("RAG TIMING: Phase 2 (parallel LLM search across %d docs) = %.3fs", len(filtered), time.monotonic() - phase2_t0)

    if not context_parts:
        logger.warning("RAG: no relevant content found across %d doc(s) for query=%r", len(doc_ids), query[:100])
        return json.dumps({"query": query, "sources": [], "content": "No relevant content found for the query."})

    logger.info("RAG: returning %d context part(s) (%d total chars) from %d source(s)",
                len(context_parts), sum(len(p) for p in context_parts), len(matched_docs))

    # Return raw context + source metadata — let the calling agent synthesize the answer
    result = json.dumps({
        "query": query,
        "sources": [{"doc_id": did, "doc_name": name} for did, name in matched_docs],
        "content": "\n\n".join(context_parts),
    })
    logger.info("RAG TIMING: Total _rag_inner = %.3fs", time.monotonic() - rag_t0)
    return result
