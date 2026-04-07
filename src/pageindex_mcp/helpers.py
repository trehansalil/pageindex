"""RAG helpers: LLM call + tree-search pipeline."""

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


async def _llm(prompt: str) -> str:
    """Call the configured OpenAI-compatible model."""
    LLM_CALLS.inc()
    start = time.monotonic()
    try:
        client = openai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        r = await client.chat.completions.create(
            model=os.environ.get("PAGEINDEX_MODEL", "gpt-4o-2024-11-20"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return r.choices[0].message.content.strip()
    finally:
        LLM_DURATION.observe(time.monotonic() - start)


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


async def _rag_inner(query: str, doc_ids: list[str]) -> str:
    context_parts: list[str] = []
    matched_docs: list[tuple[str, str]] = []  # (doc_id, doc_name) for docs that contributed context
    logger.info("RAG search starting: query=%r across %d doc(s)", query[:100], len(doc_ids))

    for doc_id in doc_ids:
        try:
            data = load_doc(doc_id)
        except ValueError:
            logger.warning("RAG: skipping missing doc %s", doc_id)
            continue

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

        raw = await _llm(search_prompt)
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
            context_parts.append(f"=== {name} ===\n{text}")
            matched_docs.append((doc_id, name))
            logger.info("RAG: doc %s — collected %d chars of context", doc_id, len(text))
        else:
            logger.warning("RAG: doc %s — no text extracted (matched=%d, missed=%d)", doc_id, len(matched), len(missed))

    if not context_parts:
        logger.warning("RAG: no relevant content found across %d doc(s) for query=%r", len(doc_ids), query[:100])
        return "No relevant content found for the query."

    logger.info("RAG: generating answer from %d context part(s) (%d total chars)",
                len(context_parts), sum(len(p) for p in context_parts))

    # Build a document summary list so the LLM knows which sources it's working with
    doc_summary = "\n".join(
        f"- {name} (doc_id: {did})" for did, name in matched_docs
    )

    answer_prompt = (
        "Answer the question using the context below.\n\n"
        "Important rules:\n"
        "- The search system already matched the query to these documents. "
        "Treat the context as relevant even if names are not an exact match.\n"
        "- Names in queries may be partial, abbreviated, or approximate "
        "(e.g. a surname-only query should match the full name). "
        "Do NOT refuse to answer because of inexact name matches.\n"
        "- Provide a thorough answer using the available information. "
        "Cite the source document name.\n\n"
        f"Question: {query}\n\n"
        f"Source documents:\n{doc_summary}\n\n"
        f"Context:\n{chr(10).join(context_parts)}"
    )
    return await _llm(answer_prompt)
