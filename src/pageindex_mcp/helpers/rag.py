"""RAG helpers: LLM call + tree-search pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from ..cache import get_doc
from ..config import settings
from ..metrics import (
    LLM_CALLS,
    LLM_DURATION,
    RAG_DURATION,
    RAG_PARSE_FAILURES,
    RAG_SEARCHES,
)
from ..script import normalize_dashes
from .flat import _flat_search_text

logger = logging.getLogger(__name__)


_FILTER_MODEL = settings.llm_filter_model
_SEARCH_MODEL = settings.llm_search_model
_ANSWER_MODEL = settings.llm_model
_SEARCH_CONCURRENCY = settings.llm_search_concurrency


async def _llm(prompt: str, model: str | None = None) -> str:
    """Call the configured OpenAI-compatible model."""
    # HR3: block query-path LLM calls when pii_corpus=True and the endpoint
    # is not ZDR-allowlisted — query responses may contain PII.
    if settings.pii_corpus:
        from ..config import require_zdr_compliance

        require_zdr_compliance(settings.openai_base_url, "RAG query")

    LLM_CALLS.inc()
    start = time.monotonic()
    try:
        from ..client import get_openai_client

        client = get_openai_client()
        resolved_model = model or _ANSWER_MODEL
        if resolved_model.startswith("azure/"):
            resolved_model = resolved_model[len("azure/") :]
        r = await client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = r.choices[0].message.content
        if content is None:
            logger.warning("LLM returned None content for prompt %s", prompt[:80])
            return ""
        return content.strip()
    finally:
        LLM_DURATION.observe(time.monotonic() - start)


def _extract_json_object(raw: str) -> str:
    """Extract the outermost JSON object from an LLM response."""
    import re

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return match.group(0)
    return raw.strip()


async def _prefilter_docs(
    query: str,
    doc_summaries: list[dict],
) -> list[str]:
    """Use a fast LLM call to select which documents are worth searching.
    Returns list of doc_ids that are potentially relevant."""
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

    clean = _extract_json_object(raw)

    try:
        parsed = json.loads(clean)
        ids = parsed.get("relevant_doc_ids", [])
        logger.info("RAG pre-filter: %d/%d docs selected: %s", len(ids), len(doc_summaries), ids)
        return ids
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("RAG pre-filter: failed to parse response, using all docs: %s", e)
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


def _parse_page_spec(pages: str) -> set[int]:
    """Parse '1-3,5' style page spec into a set of page numbers."""
    wanted: set[int] = set()
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            wanted.update(range(int(a), int(b) + 1))
        else:
            wanted.add(int(part))
    return wanted


def _extract_page_hits(structure: list, pages: str) -> list[dict]:
    """Shared page-hit extraction: build node map, parse page spec, filter by intersection."""
    nm: dict = {}
    _build_node_map(structure, nm)
    wanted = _parse_page_spec(pages)
    return [
        {
            "node_id": nid,
            "title": n.get("title"),
            "pages": f"{n.get('start_index')}-{n.get('end_index')}",
            "text": n["text"],
        }
        for nid, n in nm.items()
        if set(range(n.get("start_index", 0), n.get("end_index", 0) + 1)) & wanted and "text" in n
    ]


async def _rag(query: str, doc_ids: list[str]) -> str:
    """Run PageIndex tree-search + answer-generation pipeline."""
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

        clean = _extract_json_object(raw)

        try:
            parsed = json.loads(clean)
            ids = parsed.get("node_list", [])
            thinking = parsed.get("thinking", "")
            logger.info("RAG: doc %s — LLM selected %d node(s): %s", doc_id, len(ids), ids)
            logger.info("RAG: doc %s — LLM reasoning: %s", doc_id, thinking[:300])
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            ids = []
            RAG_PARSE_FAILURES.labels(doc_id=doc_id).inc()
            logger.warning(
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

    narrowing_t0 = time.monotonic()
    narrowed_ids = await _registry_narrow(query, doc_ids)
    if narrowed_ids is not doc_ids:
        logger.info(
            "RAG TIMING: Phase 1.4 narrowing %d -> %d doc(s) = %.3fs",
            len(doc_ids),
            len(narrowed_ids),
            time.monotonic() - narrowing_t0,
        )
        doc_ids = narrowed_ids

    phase1_t0 = time.monotonic()
    doc_load_semaphore = asyncio.Semaphore(max(1, settings.registry_query_concurrency))

    async def _load_one(doc_id: str) -> tuple[str, dict] | None:
        t = time.monotonic()
        async with doc_load_semaphore:
            try:
                data = await asyncio.to_thread(get_doc, doc_id)
            except ValueError:
                logger.warning("RAG: skipping missing doc %s", doc_id)
                return None
        logger.info("RAG TIMING: load_doc(%s) = %.3fs", doc_id, time.monotonic() - t)
        return (doc_id, data)

    loaded = await asyncio.gather(*(_load_one(doc_id) for doc_id in doc_ids))
    doc_data: dict[str, dict] = dict(filter(None, loaded))
    logger.info(
        "RAG TIMING: Phase 1 (load %d docs) = %.3fs", len(doc_data), time.monotonic() - phase1_t0
    )

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

    result = json.dumps(
        {
            "query": query,
            "sources": [{"doc_id": did, "doc_name": name} for did, name in matched_docs],
            "content": "\n\n".join(context_parts),
        }
    )
    logger.info("RAG TIMING: Total _rag_inner = %.3fs", time.monotonic() - rag_t0)
    return result


_REGISTRY_COMPLETE_TTL_S = 60.0
_registry_complete_cache: bool = False
_registry_complete_cache_ts: float = 0.0


async def _check_registry_complete_cached() -> bool:
    """RFC-008 D1 (ISS-07): shared registry-complete check with a 60s cache."""
    global _registry_complete_cache, _registry_complete_cache_ts

    now = time.monotonic()
    if _registry_complete_cache and (now - _registry_complete_cache_ts) < _REGISTRY_COMPLETE_TTL_S:
        return True

    from ..cache import get_async_redis
    from ..registry import is_registry_complete

    try:
        r = await get_async_redis()
        complete = await is_registry_complete(r)
    except Exception as exc:
        logger.warning(
            "_check_registry_complete_cached: Redis error checking registry flag: %s", exc
        )
        return False

    if complete:
        _registry_complete_cache = True
        _registry_complete_cache_ts = now

    return complete


async def _registry_narrow(query: str, doc_ids: list[str]) -> list[str]:
    """RFC-006 Phase 1.4: narrow ``doc_ids`` via Stage A (facet) then Stage B (BM25)."""
    if not settings.registry_enabled or not settings.postgres_dsn:
        return doc_ids

    from ..registry import get_pool, stage_a_filter, stage_b_candidates

    pool = get_pool()
    if pool is None:
        return doc_ids

    complete = await _check_registry_complete_cached()
    if not complete:
        return doc_ids

    doc_id_set = set(doc_ids)

    stage_a = await stage_a_filter(query)
    if stage_a is not None:
        stage_a_ids = [r["doc_id"] for r in stage_a if r["doc_id"] in doc_id_set]
        if stage_a_ids:
            logger.info(
                "_registry_narrow: Stage A hit — %d/%d docs match facets",
                len(stage_a_ids),
                len(doc_ids),
            )
            doc_id_set = set(stage_a_ids)

    topk = settings.catalog_topk
    stage_b = await stage_b_candidates(query, topk)
    if stage_b is None:
        logger.warning("_registry_narrow: Stage B failed — using full doc_ids list")
        return doc_ids

    narrowed = [r["doc_id"] for r in stage_b if r["doc_id"] in doc_id_set]
    if not narrowed:
        logger.warning(
            "_registry_narrow: Stage B returned no overlap with caller's doc set — "
            "falling back to full %d-doc list",
            len(doc_ids),
        )
        return doc_ids

    logger.info(
        "_registry_narrow: narrowed %d -> %d doc(s) via registry (topk=%d)",
        len(doc_ids),
        len(narrowed),
        topk,
    )
    return narrowed
