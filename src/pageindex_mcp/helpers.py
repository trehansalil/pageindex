"""RAG helpers: LLM call + tree-search pipeline."""

import asyncio
import json
import logging
import re
import time
import unicodedata
from collections import Counter

from .cache import get_doc
from .config import settings
from .converters import normalize_dashes
from .metrics import (
    LLM_CALLS,
    LLM_DURATION,
    RAG_DURATION,
    RAG_PARSE_FAILURES,
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
        content = r.choices[0].message.content
        if content is None:
            logger.warning("LLM returned None content for prompt %s", prompt[:80])
            return ""
        return content.strip()
    finally:
        LLM_DURATION.observe(time.monotonic() - start)


def _extract_json_object(raw: str) -> str:
    """Extract the outermost JSON object from an LLM response.

    LLMs frequently wrap JSON in markdown ```json fences or surround it with
    prose ("Here is the result: {...}"). Grab the ``{...}`` span so the
    downstream ``json.loads`` sees clean JSON. Falls back to the stripped input
    when no braces are present (``json.loads`` then raises and the caller handles
    the failure). Shared by ``_prefilter_docs`` (RFC-008 D6) and
    ``_search_one_doc`` (RFC-008 D7) — both need identical fence/prose-tolerant
    extraction.
    """
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return match.group(0)
    return raw.strip()


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

    # --- Phase 1.4: Registry narrowing (RFC-006 Stage A + Stage B) ---
    # Runs BEFORE Phase 1 (doc load) to avoid loading docs that won't survive the
    # LLM prefilter.  Only activates when the registry pool is ready and the
    # registry_complete flag is set (same conditions as _list_docs_with_fallback).
    # Falls through silently when unavailable — doc_ids is unchanged.
    narrowing_t0 = time.monotonic()
    narrowed_ids = await _registry_narrow(query, doc_ids)
    if narrowed_ids is not doc_ids:  # narrowing actually happened
        logger.info(
            "RAG TIMING: Phase 1.4 narrowing %d -> %d doc(s) = %.3fs",
            len(doc_ids),
            len(narrowed_ids),
            time.monotonic() - narrowing_t0,
        )
        doc_ids = narrowed_ids

    # --- Phase 1: Load all documents (RFC audit Issue C #1: bounded fan-out) ---
    # get_doc() is a blocking Redis/MinIO read; previously this ran sequentially
    # for up to catalog_topk (default 200) docs. Fan out via to_thread + Semaphore
    # so the wall-clock is dominated by the slowest single load, not the sum.
    phase1_t0 = time.monotonic()
    # max(1, ...) defends against a misconfigured 0/negative value even though
    # config._load_settings() already clamps it — a non-positive semaphore
    # would deadlock every document load forever.
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


_REGISTRY_COMPLETE_TTL_S = 60.0
_registry_complete_cache: bool = False
_registry_complete_cache_ts: float = 0.0


async def _check_registry_complete_cached() -> bool:
    """RFC-008 D1 (ISS-07): shared registry-complete check with a 60s cache.

    Uses the ``cache.py`` singleton (``get_async_redis``) instead of opening
    an ad-hoc connection per call, and caches a positive result for
    ``_REGISTRY_COMPLETE_TTL_S`` seconds. The flag is monotonic — it flips
    ``False`` -> ``True`` exactly once, when the initial registry backfill
    finishes — so caching ``True`` is safe and avoids a Redis round-trip in
    the steady-state case. A cached ``False`` is never trusted (it may flip
    to ``True`` at any time), so every call re-checks Redis until the flag is
    observed ``True``.

    Shared by ``_registry_narrow`` (this module) and
    ``tools.documents._list_docs_with_fallback``.
    """
    global _registry_complete_cache, _registry_complete_cache_ts

    now = time.monotonic()
    if _registry_complete_cache and (now - _registry_complete_cache_ts) < _REGISTRY_COMPLETE_TTL_S:
        return True

    from .cache import get_async_redis
    from .registry import is_registry_complete

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
    """RFC-006 Phase 1.4: narrow ``doc_ids`` via Stage A (facet) then Stage B (BM25).

    Returns the original ``doc_ids`` list (same object) when narrowing is
    unavailable (pool not ready, registry not complete, Postgres error, or
    registry_enabled=False) so the caller falls through to loading all docs —
    the existing behaviour.

    Stage A is a no-op until Tier-1 node-metadata fields land (all
    _KNOWN_FACETS sets are empty → stage_a_filter returns None).

    Stage B cuts to PAGEINDEX_CATALOG_TOPK via ts_rank/GIN.  The result is
    intersected with ``doc_ids`` so only docs the caller already knows about
    are returned — prevents the registry from introducing docs the caller
    hasn't listed (e.g. after a partial backfill).
    """
    if not settings.registry_enabled or not settings.postgres_dsn:
        return doc_ids

    from .registry import get_pool, stage_a_filter, stage_b_candidates

    pool = get_pool()
    if pool is None:
        return doc_ids

    # Honour the same backfill-complete gate as the listing path.
    complete = await _check_registry_complete_cached()
    if not complete:
        return doc_ids

    doc_id_set = set(doc_ids)

    # Stage A — exact facet filter (no-op pre-Tier-1).
    stage_a = await stage_a_filter(query)
    if stage_a is not None:
        stage_a_ids = [r["doc_id"] for r in stage_a if r["doc_id"] in doc_id_set]
        if stage_a_ids:
            logger.info(
                "_registry_narrow: Stage A hit — %d/%d docs match facets",
                len(stage_a_ids),
                len(doc_ids),
            )
            # Still run Stage B on the facet-filtered set to apply topK rank ordering.
            doc_id_set = set(stage_a_ids)

    # Stage B — BM25 lexical ranking.
    topk = settings.catalog_topk
    stage_b = await stage_b_candidates(query, topk)
    if stage_b is None:
        # Postgres error in Stage B — fall through to full load.
        logger.warning("_registry_narrow: Stage B failed — using full doc_ids list")
        return doc_ids

    # Intersect with the caller's doc_id_set (post Stage A filter if it fired).
    narrowed = [r["doc_id"] for r in stage_b if r["doc_id"] in doc_id_set]
    if not narrowed:
        # No overlap — stage B returned docs the caller doesn't know about yet
        # (pre-backfill gap) or the query is very unusual.  Fall back to full set.
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


def _flatten_tree_text(nodes: list) -> str:
    """Concatenate all title+text from a tree structure into a single string."""
    parts: list[str] = []

    def _walk(ns: list) -> None:
        for n in ns:
            parts.append(str(n.get("title", "")))
            parts.append(str(n.get("text", "")))
            _walk(n.get("nodes") or [])

    _walk(nodes)
    return "".join(parts)


def _is_garbled_blob(blob: str) -> bool:
    """Unified garble-detection heuristics (D7 / RFC-013).

    Checks (in order): empty, null/replacement bytes, GLYPH< markers,
    control-char ratio >5%, PUA ratio >3%, digit ratio >60% (blobs >500 chars),
    token repetition >30% (>20 alnum tokens, excluding symbolic tokens)."""
    if not blob.strip():
        return True
    if "\x00" in blob or "\ufffd" in blob:
        return True
    if "GLYPH<" in blob:
        return True
    bad = sum(1 for c in blob if ord(c) < 32 and c not in "\n\r\t")
    if (bad / len(blob)) > 0.05:
        return True
    # PUA-char ratio > 3% — font/CMap mojibake
    pua = sum(1 for c in blob if 0xE000 <= ord(c) <= 0xF8FF)
    if (pua / len(blob)) > 0.03:
        return True
    # Digit ratio > 60% on blobs > 500 chars — numeric junk
    if len(blob) > 500:
        digits = sum(1 for c in blob if c.isdigit())
        if (digits / len(blob)) > 0.60:
            return True
    # Single-token repetition > 30% on blobs with enough tokens. Purely
    # symbolic tokens ('|' table delimiters, '€'/currency signs) are excluded:
    # a wide price table legitimately produces dozens of these per row, which
    # is not garbling.
    tokens = [t for t in blob.split() if any(c.isalnum() for c in t)]
    if len(tokens) > 20:
        most_common_count = Counter(tokens).most_common(1)[0][1]
        if (most_common_count / len(tokens)) > 0.30:
            return True
    return False


# RFC-015 D8: sparse mixed-script mojibake. Bulk-ratio garble checks (PUA%,
# digit%, repetition%) dilute away a handful of corrupted Latin fragments glued
# to Arabic across a long document, so OCR escalation never fires. This
# length-independent per-node pattern catches Arabic-Latin-Arabic and
# Latin-Arabic-Latin fragments directly.
# NOTE (RFC-015 D8): the design sketch wrote the ASCII class as [\x20-\x7E], but
# \x20 is SPACE — including it makes "[Arabic][space][Arabic]" match every
# inter-word gap in normal Arabic prose (clean Arabic scores ratio ~0.9, well
# above the 0.02 threshold), which would flag EVERY Arabic document as garbled and
# contradicts the design's own calibration (b1a72fb2 legitimate Arabic must NOT
# trigger). "Glued" fragments are by definition whitespace-free, so the class is
# \x21-\x7E (printable ASCII, no space). See PENDING_DECISIONS [GAP] D8-space.
_MIXED_SCRIPT_RE = re.compile(
    r"[؀-ۿ][\x21-\x7E]{1,8}[؀-ۿ]"  # Arabic-Latin-Arabic (glued, no space)
    r"|[\x21-\x7E]{1,8}[؀-ۿ][\x21-\x7E]{1,8}"  # Latin-Arabic-Latin (glued, no space)
)


def _has_sparse_mojibake(text: str, threshold: float = 0.02) -> bool:
    """RFC-015 D8: detect localized Latin/digit fragments glued to Arabic script.

    Requires >100 chars and >``threshold`` (2%) of whitespace-tokens matching the
    Arabic-Latin-Arabic / Latin-Arabic-Latin pattern. Calibrated against 92eebefa
    (21.4% mixed-script — must trigger) while sparing b1a72fb2 (legitimate
    transliterated names — below 2%). Additive-only: OR'd into the existing garble
    gates, so it can flag MORE text as garbled but never un-flag text the bulk
    heuristics already caught (HR5-tightening)."""
    if len(text) < 100:
        return False
    matches = _MIXED_SCRIPT_RE.findall(text)
    return (len(matches) / max(len(text.split()), 1)) > threshold


def _tree_is_garbled(nodes: list) -> bool:
    blob = _flatten_tree_text(nodes)
    # Additive OR (RFC-015 D8): existing bulk heuristics first, then sparse
    # mixed-script. Never narrows the existing gate.
    return _is_garbled_blob(blob) or _has_sparse_mojibake(blob)


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
    # RFC-015 D2 (HR5 tightening): reject content-ordering regressions. A caller
    # surfaces this reason as a low_quality_tree error rather than persisting.
    if _tree_is_reordered(structure):
        return False, "reordered"
    return True, ""


# ── RFC-014 D1: verdict computation helpers ─────────────────────────────────────


def _walk_leaves(structure: list):
    """Yield each leaf node dict (no ``nodes`` children) in document order."""
    for n in structure or []:
        if not isinstance(n, dict):
            continue
        children = n.get("nodes") or []
        if children:
            yield from _walk_leaves(children)
        else:
            yield n


def _tree_max_leaf_ratio(structure: list) -> tuple[int, int, float]:
    # RFC-015 D3A: denominator is LEAF chars only. Summing non-leaf wrapper
    # titles into `total` inflated the denominator and deflated the ratio,
    # masking over-nested "staircase" trees (a4c1b522). Leaf-only is a strict
    # tightening — the ratio can only rise, never fall, so no previously-failing
    # tree can newly PASS.
    max_leaf = 0
    total = 0
    for leaf in _walk_leaves(structure):
        chars = len(leaf.get("title", "")) + len(leaf.get("text", ""))
        total += chars
        max_leaf = max(max_leaf, chars)

    ratio = max_leaf / total if total > 0 else 0.0
    return max_leaf, total, ratio


def _tree_is_reordered(structure: list) -> bool:
    """RFC-015 D2: True if any leaf's start_index (fallback line_num) regresses
    below the running max seen so far — i.e. content emitted out of source order
    (54e92c0a: a span emitted after the document's final article)."""
    running_max: int | None = None
    for leaf in _walk_leaves(structure):
        idx = leaf.get("start_index", leaf.get("line_num"))
        if idx is None:
            continue
        if running_max is not None and idx < running_max:
            return True
        running_max = idx if running_max is None else max(running_max, idx)
    return False


def ocr_noise_ratio(text: str) -> float:
    if not text:
        return 0.0
    noise = sum(
        1
        for c in text
        if c == "�" or 0xE000 <= ord(c) <= 0xF8FF or (ord(c) < 32 and c not in "\n\r\t")
    )
    return noise / len(text)


def hash_pipe_ratio(text: str) -> float:
    if not text:
        return 0.0
    count = sum(1 for c in text if c in "#|")
    return count / len(text)


def classify_verdict(
    structure: list,
    content_class: str,
    validate_reason: str | None,
) -> tuple[str, str]:
    if validate_reason == "garbling":
        return "FAIL", "garbling"
    # RFC-015 D2: content-ordering regression forces the lowest tier. Self-contained
    # (checks the structure directly) so it holds even when validate_reason is None.
    if validate_reason == "reordered" or _tree_is_reordered(structure):
        return "FAIL", "reordered"

    _, _, max_leaf_ratio = _tree_max_leaf_ratio(structure)
    if max_leaf_ratio > 0.75:
        return "FAIL", f"max_leaf_ratio={max_leaf_ratio:.2f}"

    node_count = _tree_node_count(structure)
    depth = _tree_depth(structure)
    garbled = _tree_is_garbled(structure)

    if node_count >= 3 and depth >= 2 and max_leaf_ratio < 0.15 and not garbled:
        return "PASS", ""

    # Base verdict is MARGINAL — try category-specific promotion.
    # Category B/C use the wider 0.17 threshold (RFC-014 D4).
    from .config import CATEGORY_BC_PROMOTION_THRESHOLD

    flat_text = _flatten_tree_text(structure)

    if content_class.startswith("ocr_"):
        if max_leaf_ratio < 0.15 and ocr_noise_ratio(flat_text) < 0.005:
            return "PASS", "cat_a_promoted"
    elif content_class.startswith("flat_"):
        if max_leaf_ratio < CATEGORY_BC_PROMOTION_THRESHOLD and node_count >= 3:
            return "PASS", "cat_b_promoted"
    else:
        if (
            not garbled
            and hash_pipe_ratio(flat_text) < 0.01
            and max_leaf_ratio < CATEGORY_BC_PROMOTION_THRESHOLD
        ):
            return "PASS", "cat_c_promoted"

    # Build descriptive reason for remaining MARGINAL
    if garbled:
        reason = "garbling"
    elif node_count < 3:
        reason = f"node_count={node_count}"
    elif depth < 2:
        reason = f"depth={depth}"
    else:
        reason = f"leaf_concentration={max_leaf_ratio:.2f}"
    return "MARGINAL", reason


def detect_regression(
    structure: list,
    prev_node_count: int | None,
    prev_max_leaf_ratio: float | None,
) -> bool:
    """Category E regression gate (RFC-014 D4, Property 6).

    Returns True when BOTH conditions hold vs. the last stored verdict:
      - node_count dropped >30%
      - max_leaf_ratio grew >2x
    """
    if prev_node_count is None or prev_max_leaf_ratio is None:
        return False
    if prev_node_count == 0:
        return False
    cur_count = _tree_node_count(structure)
    _, _, cur_ratio = _tree_max_leaf_ratio(structure)
    count_dropped = cur_count < prev_node_count * 0.7
    ratio_grew = prev_max_leaf_ratio > 0 and cur_ratio > prev_max_leaf_ratio * 2
    return count_dropped and ratio_grew


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
_FLAT_FIGURE_RE = re.compile(r"^\[Figure:\s*fig-(\d+)(?:\s*\|\s*(.*?))?\]$")
_FLAT_CHART_TEXT_RE = re.compile(r"^>\s*\[Chart text\]:\s*(.+)$")


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


def _forward_fill_leading_column(rows: list[list[str]]) -> list[list[str]]:
    """RFC-015 D9: forward-fill empty cells in COLUMN 0 only, from the most recent
    non-empty column-0 value (merged rowspan header labels — e544d939 Katze table,
    where a merged ``Selbstbehalt`` label is dropped from 22 data rows).

    Column 0 exclusively — data columns (index 1+) are never modified, mirroring
    the RFC's explicit anti-goal of not corrupting data columns. Mutates ``rows``
    in place and returns it."""
    last_val = ""
    for row in rows:
        if not row:
            continue
        if row[0].strip():
            last_val = row[0].strip()
        elif last_val:
            row[0] = last_val
    return rows


def _flat_parse_table(lines: list[str], start: int) -> tuple[dict, int]:
    """Parse a markdown table beginning at `start` (a header row followed by a
    separator). Returns (table_block, next_index)."""
    header = _flat_split_pipe_row(lines[start])
    i = start + 2  # skip header + separator
    data_rows: list[list[str]] = []
    while i < len(lines) and _flat_is_pipe_row(lines[i]) and not _flat_is_separator_row(lines[i]):
        data_rows.append(_flat_split_pipe_row(lines[i]))
        i += 1
    # RFC-015 D9: forward-fill merged rowspan labels in column 0 before
    # verbalization, so both the structured `rows` matrix and the `row_records`
    # carry the recovered label. Applied to DATA rows only (the header row keeps
    # its own column titles); column 0 only (data columns untouched).
    data_rows = _forward_fill_leading_column(data_rows)
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
# Each digit capture allows an optional decimal suffix ("Article 3.1", "المادة
# ٣.١") so sub-numbered sequences don't all truncate to the same integer ordinal
# and collapse the strictly-increasing-run guard below (_ordinal_value parses the
# capture as a float).
_OVERSIZED_ORDINAL_RE = re.compile(
    r"(?:"
    r"§\s*\(?\s*(?P<sec>\d+(?:\.\d+)?)"  # § 12 / § (12) / § 12.1
    r"|Art(?:icle|\.)?\s+\(?\s*(?P<art>\d+(?:\.\d+)?)"  # Article 9 / Art. 9 / Article (9)
    r"|Section\s+\(?\s*(?P<s>\d+(?:\.\d+)?)"  # Section 4 / Section (4) / Section 4.2
    r"|Schedule\s+\(?\s*(?P<sched>\d+(?:\.\d+)?)"  # RFC-015 D5b: Schedule 3 / Schedule (3)
    r"|(?:ال)?مادة\s*\(?\s*(?P<mada>[\d٠-٩]+(?:[.٫][\d٠-٩]+)?)"  # (ال)مادة (5) / المادة ٥
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
_ARABIC_INDIC = {ord(d): ord(a) for d, a in zip("٠١٢٣٤٥٦٧٨٩", "0123456789", strict=True)}

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


def _ordinal_value(m: "re.Match[str]") -> tuple[int, ...]:
    """The ordinal captured by whichever marker alternative matched, as a tuple of
    dotted components compared lexicographically (NOT a float — ``3.10`` must
    stay distinct from ``3.1``, whereas ``float("3.10") == float("3.1")`` would
    silently collapse them and eject a genuine heading from the increasing run)."""
    digits = (
        m.group("art")
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


_PREAMBLE_MIN_CHARS = 50


def _synthesize_preamble_node(md_text: str, tree: dict) -> dict:
    """RFC-015 D10: recover body text that precedes a document's first heading.

    ``md_to_tree`` (the vendored fork's tree-builder) starts building nodes only
    from the first heading match, so any content before it — e.g. the "who is
    covered" clause preceding Section 1 in doc 722eb392 (GHV Reitlehrer
    Haftpflicht) — is silently dropped. When that leading preamble (stripped)
    exceeds ``_PREAMBLE_MIN_CHARS``, this synthesizes a node (mirroring the
    ``_apply_split`` node shape: ``title``, ``text``, ``nodes``, ``node_id``,
    optional ``start_index``/``end_index``) and prepends it to
    ``tree["structure"]`` at index 0, before any other synthesis/promotion.

    Purely additive: a document whose first heading is already at line 1 (no
    preamble) or whose document has no heading at all gets no new node, and
    ``tree`` is returned unchanged (mutated in place when a splice happens).
    """
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
        # No heading anywhere: headingless docs are already handled by the
        # existing flat-path/tree logic — this fix does not apply.
        return tree

    if first_heading_idx == 0:
        # First heading is already the very first line: no preamble.
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
    """RFC-015 D5a: lightweight check for any ``_OVERSIZED_ORDINAL_RE`` marker.

    Matched on the same NFKC-folded copy the splitter itself uses (so presentation-
    form Arabic and Latin paren forms are seen), so this agrees exactly with the
    marker-finding done inside ``split_oversized_leaf_nodes``. Used to decouple the
    split trigger from raw char count: a residual leaf under ``max_chars`` that
    still carries a real heading sequence (6147c7d7: 19,959 chars) must still be
    eligible for splitting."""
    if not text:
        return False
    folded, _ = _fold_with_index_map(text)
    return _OVERSIZED_ORDINAL_RE.search(folded) is not None


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
        # RFC-015 D5a: decouple the split trigger from raw size. A leaf is
        # split-eligible when it is oversized OR carries detectable heading
        # markers — a marker-dense residual leaf under max_chars (6147c7d7,
        # 19,959 chars) still collapses a real hierarchy and must be split.
        # Strictly ADDITIVE: only widens the eligible set, never removes an
        # oversized leaf from it, and every downstream guard (frontmatter-ToC
        # accept, strictly-increasing-run ≥ min_segments, paragraph-fallback
        # acceptance) is unchanged, so no leaf is split without a genuine
        # ordinal sequence (HR5-neutral: recovers more real structure only).
        if len(text) <= max_chars and not _has_heading_markers(text):
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
_ARABIC_SCRIPT_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
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
    rows, `anchor` itself is a keyed table (at least one non-numeric row-label
    header), AND all of `cont`'s headers are date/numeric-like (no row-label
    column). The anchor check prevents two consecutive numeric-header tables
    (neither of which has a label column) from being merged as if one continued
    the other."""
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
            j < n and blocks[j].get("role") == "table" and _is_continuation_table(anchor, blocks[j])
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


_TOC_DOT_LEADER_RE = re.compile(r"\.{4,}\s*\d+\s*\|?\s*$")


def _flat_text_is_garbled(md: str) -> bool:
    """Garble gate for flat-path markdown (mirrors _tree_is_garbled heuristics)."""
    text = md or ""
    # Additive OR (RFC-015 D8): sparse mixed-script mojibake, same as the tree gate.
    return _is_garbled_blob(text) or _has_sparse_mojibake(text)


def _looks_like_toc_page(block_text: str) -> bool:
    """Return True if text looks like a table-of-contents page (dot-leader lines)."""
    text_lines = block_text.splitlines()
    if len(text_lines) < 3:
        return False
    matches = sum(1 for ln in text_lines if _TOC_DOT_LEADER_RE.search(ln))
    return (matches / len(text_lines)) > 0.40


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

        # Figure marker -> image block. Consumes an optional [Chart text]
        # blockquote on the following non-blank line.
        m_fig = _FLAT_FIGURE_RE.match(stripped)
        if m_fig:
            flush_prose()
            fig_index = int(m_fig.group(1))
            fig_desc = (m_fig.group(2) or "").strip()
            ocr_text = ""
            # Peek ahead for > [Chart text]: ...
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
