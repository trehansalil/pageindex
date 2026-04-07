"""RAG helpers: LLM call + tree-search pipeline."""

import json
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

    for doc_id in doc_ids:
        try:
            data = load_doc(doc_id)
        except ValueError:
            continue

        tree = data.get("structure", [])
        name = data.get("doc_name", data.get("filename", doc_id))
        tree_slim = _strip_text(tree)

        nm: dict = {}
        _build_node_map(tree, nm)

        search_prompt = (
            "You are given a question and a document tree.\n"
            "Each node has a node_id, title, and summary.\n"
            "Find all node_ids whose content likely answers the question.\n\n"
            f"Question: {query}\n"
            f"Document: {name}\n"
            f"Tree:\n{json.dumps(tree_slim, indent=2)}\n\n"
            'Reply ONLY in JSON: {"thinking": "<reasoning>", "node_list": ["id1", "id2"]}'
        )

        raw = await _llm(search_prompt)

        clean = re.sub(r"^```[a-z]*\n?", "", raw.strip())
        clean = re.sub(r"\n?```$", "", clean).strip()

        try:
            ids = json.loads(clean).get("node_list", [])
        except Exception:
            ids = []

        text = "\n\n".join(
            nm[i]["text"] for i in ids if i in nm and "text" in nm[i]
        )
        if text:
            context_parts.append(f"=== {name} ===\n{text}")

    if not context_parts:
        return "No relevant content found for the query."

    answer_prompt = (
        "Answer the question based only on the context below.\n\n"
        f"Question: {query}\n\n"
        f"Context:\n{chr(10).join(context_parts)}"
    )
    return await _llm(answer_prompt)
