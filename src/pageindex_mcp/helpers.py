"""RAG helpers: LLM call + tree-search pipeline."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterator

from .cache import get_doc
from .config import settings
from .metrics import (
    LLM_CALLS,
    LLM_DURATION,
    RAG_DURATION,
    RAG_PARSE_FAILURES,
    RAG_SEARCHES,
    TOC_STRIP_SKIPPED,
)

# _JOINING_TYPE is unused here directly; re-exported because tests import it
# from pageindex_mcp.helpers rather than pageindex_mcp.script.
from .script import (
    _JOINING_TYPE as _JOINING_TYPE,
)
from .script import (
    AR_RUN_RE,
    ARABIC_RANGES,
    BlobKind,
    PRESENTATION_RANGES,
    RtlDecision,
    _word_has_reversed_morphology,
    decide_rtl,
    normalize_dashes,
    normalize_for_garble,
)
from .script import (
    arabic_readability_score as _arabic_readability_score,
)
from .script import (
    is_arabic_char as _is_arabic_char,
)

logger = logging.getLogger(__name__)


_FILTER_MODEL = settings.llm_filter_model
_SEARCH_MODEL = settings.llm_search_model
_ANSWER_MODEL = settings.llm_model
_SEARCH_CONCURRENCY = settings.llm_search_concurrency


# ---------------------------------------------------------------------------
# Zone-1: Typed reason protocol for validate_tree
# ---------------------------------------------------------------------------


class TreeDefect(StrEnum):
    OK = ""
    GARBLING = "garbling"
    NODE_GARBLING = "node_garbling"
    NODE_COUNT_LOW = "node_count<3"
    DEPTH_LOW = "depth<2"
    REORDERED = "reordered"
    RTL_REVERSAL = "rtl_reversal"
    BIDI_DEGRADED = "bidi_degraded"
    EMPTY_NODE_CONTAMINATION = "empty_node_contamination"
    LOW_CONTENT_DENSITY = "low_content_density"
    SUSPECT_DENSITY = "suspect_density"
    ARABIC_LOW_CONTENT_RATIO = "arabic_low_content_ratio"  # deprecated: dead gate (strict subset of GARBLING); kept for persisted verdict_reason compat


@dataclass(frozen=True)
class TreeGateResult:
    ok: bool
    defect: TreeDefect
    detail: str = ""
    signals: TreeSignals | None = None

    def __str__(self) -> str:
        if self.detail:
            return f"{self.defect.value}({self.detail})"
        return self.defect.value

    def __iter__(self) -> Iterator[bool | str]:
        """Yield (ok, reason_str) for backward-compat tuple unpacking.

        ``signals`` is intentionally excluded from iteration so that
        ``ok, reason = validate_tree(...)`` keeps working at all call sites.
        """
        yield self.ok
        yield str(self)


@dataclass(frozen=True)
class ExtractionSnapshot:
    """Zone-5: frozen snapshot of pre-retry extraction state.

    Consolidates the six+ separate ``pre_retry_*`` variables into a single
    immutable object.  ``first_defect`` is intentionally excluded -- it is
    write-once per extraction attempt and must NOT be reverted on retry loss.
    """
    result: dict
    ok: bool
    defect: TreeDefect
    reason_str: str
    gate_result: TreeGateResult | None
    total_chars: int
    md_content: str | None
    pic_results: list
    used_converter: str | None

    @classmethod
    def from_state(
        cls,
        *,
        result: dict,
        ok: bool,
        defect: TreeDefect,
        reason_str: str,
        gate_result: TreeGateResult | None,
        total_chars: int,
        md_content: str | None,
        pic_results: list,
        used_converter: str | None,
    ) -> "ExtractionSnapshot":
        return cls(
            result=result,
            ok=ok,
            defect=defect,
            reason_str=reason_str,
            gate_result=gate_result,
            total_chars=total_chars,
            md_content=md_content,
            pic_results=pic_results,
            used_converter=used_converter,
        )

    def restore(self) -> tuple:
        """Return all fields as a tuple for easy destructuring.

        Usage::

            result, ok, reason, gate_result, original_gate_result, \
                md_content, pic_results, used_converter = snapshot.restore()
            total_chars = snapshot.total_chars

        ``gate_result`` appears twice (for both ``gate_result`` and
        ``original_gate_result``) because both must revert to the
        pre-retry value on a lost retry.
        """
        return (
            self.result,
            self.ok,
            self.reason_str,
            self.gate_result,
            self.gate_result,  # original_gate_result
            self.md_content,
            self.pic_results,
            self.used_converter,
        )


class _ReasonPolicy(StrEnum):
    RAISE = "raise"
    RETRY_OCR = "retry_ocr"
    RETRY_RTL = "retry_rtl"
    PERSIST_FAIL = "persist_fail"
    CAP_MARGINAL = "cap_marginal"
    OK = "ok"


REASON_POLICY: dict[TreeDefect, _ReasonPolicy] = {
    TreeDefect.OK: _ReasonPolicy.OK,
    TreeDefect.GARBLING: _ReasonPolicy.RETRY_OCR,
    TreeDefect.NODE_GARBLING: _ReasonPolicy.RETRY_OCR,
    TreeDefect.NODE_COUNT_LOW: _ReasonPolicy.RAISE,
    TreeDefect.DEPTH_LOW: _ReasonPolicy.RAISE,
    TreeDefect.REORDERED: _ReasonPolicy.RAISE,
    TreeDefect.RTL_REVERSAL: _ReasonPolicy.RETRY_RTL,
    TreeDefect.BIDI_DEGRADED: _ReasonPolicy.CAP_MARGINAL,
    TreeDefect.EMPTY_NODE_CONTAMINATION: _ReasonPolicy.PERSIST_FAIL,
    TreeDefect.LOW_CONTENT_DENSITY: _ReasonPolicy.PERSIST_FAIL,
    TreeDefect.SUSPECT_DENSITY: _ReasonPolicy.PERSIST_FAIL,
    TreeDefect.ARABIC_LOW_CONTENT_RATIO: _ReasonPolicy.PERSIST_FAIL,
}

assert set(REASON_POLICY) == set(TreeDefect), (
    f"REASON_POLICY missing: {set(TreeDefect) - set(REASON_POLICY)}"
)

# Hard-fail defects: any of these returned by validate_tree causes
# classify_verdict to return FAIL immediately, before content-class dispatch.
# Derived from the GROUP-1 dispatches in classify_verdict; kept in sync with
# REASON_POLICY (PERSIST_FAIL entries + GARBLING + REORDERED).
HARD_FAIL_DEFECTS: frozenset[TreeDefect] = frozenset({
    TreeDefect.GARBLING,
    TreeDefect.REORDERED,
    TreeDefect.EMPTY_NODE_CONTAMINATION,
    TreeDefect.LOW_CONTENT_DENSITY,
    TreeDefect.SUSPECT_DENSITY,
    TreeDefect.ARABIC_LOW_CONTENT_RATIO,
})


# ---------------------------------------------------------------------------
# Zone-5: Route StrEnum + decide_route — sole routing decider
# ---------------------------------------------------------------------------


class Route(StrEnum):
    TREE = "tree"
    FLAT = "flat"
    REJECT = "reject"
    PERSIST_FAIL = "persist_fail"


def decide_route(defect: TreeDefect, flat_routing_enabled: bool = True) -> Route:
    """Determine the extraction route from a :class:`TreeDefect`.

    Performs an exhaustive :data:`REASON_POLICY` lookup:

    * OK / CAP_MARGINAL -> TREE (tree is usable)
    * RETRY_OCR -> TREE (retry handled upstream)
    * RETRY_RTL -> FLAT when ``flat_routing_enabled`` (RFC-036 D3: flat
      is the fallback after RTL repair exhausted), else REJECT
    * RAISE -> FLAT when ``flat_routing_enabled`` and defect is
      NODE_COUNT_LOW or DEPTH_LOW, else REJECT
    * PERSIST_FAIL -> PERSIST_FAIL

    This is the **sole** routing decider -- no competing definitions.
    """
    policy = REASON_POLICY[defect]
    if policy == _ReasonPolicy.OK:
        return Route.TREE
    if policy == _ReasonPolicy.RETRY_OCR:
        return Route.TREE  # retry handled upstream
    if policy == _ReasonPolicy.RETRY_RTL:
        # After retry exhausted, flat is the fallback (RFC-036 D3)
        return Route.FLAT if flat_routing_enabled else Route.REJECT
    if policy == _ReasonPolicy.CAP_MARGINAL:
        return Route.TREE
    if policy == _ReasonPolicy.PERSIST_FAIL:
        return Route.PERSIST_FAIL
    if policy == _ReasonPolicy.RAISE:
        if flat_routing_enabled and defect in (
            TreeDefect.NODE_COUNT_LOW,
            TreeDefect.DEPTH_LOW,
        ):
            return Route.FLAT
        return Route.REJECT
    assert False, f"unhandled policy {policy!r} for defect {defect!r}"  # exhaustiveness


# ---------------------------------------------------------------------------
# Zone-2: Typed verdict signals + threshold snapshot for classify_verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictThresholds:
    hard_fail_max_leaf_ratio: float
    pass_max_leaf_ratio: float
    hysteresis_band: float
    garble_threshold: float
    cat_bc_promotion_threshold: float
    min_image_promoted_chars: int
    min_flat_promotion_chars: int
    small_doc_enabled: bool
    small_doc_leaf_ratio_bound_low: float
    small_doc_leaf_ratio_bound_high: float

    @classmethod
    def from_env(cls) -> "VerdictThresholds":
        from .config import CATEGORY_BC_PROMOTION_THRESHOLD

        return cls(
            hard_fail_max_leaf_ratio=0.75,
            pass_max_leaf_ratio=float(os.environ.get("PASS_MAX_LEAF_RATIO", "0.30")),
            hysteresis_band=float(os.environ.get("PASS_HYSTERESIS_BAND", "0.10")),
            garble_threshold=float(os.environ.get("GARBLE_WINDOW_RATIO_THRESHOLD", "0.05")),
            cat_bc_promotion_threshold=CATEGORY_BC_PROMOTION_THRESHOLD,
            min_image_promoted_chars=int(os.environ.get("MIN_IMAGE_PROMOTED_CHARS", "500")),
            min_flat_promotion_chars=int(os.environ.get("MIN_FLAT_PROMOTION_CHARS", "500")),
            small_doc_enabled=os.environ.get("SMALL_DOC_PROMOTION_ENABLED", "true").lower() == "true",
            small_doc_leaf_ratio_bound_low=0.20,
            small_doc_leaf_ratio_bound_high=0.40,
        )


# Module-level cache for VerdictThresholds — avoids re-reading env vars on
# every classify_verdict call.  Use reset_verdict_thresholds() in tests that
# manipulate threshold env vars.
_verdict_thresholds_cache: VerdictThresholds | None = None


def _get_verdict_thresholds() -> VerdictThresholds:
    """Return the cached VerdictThresholds, populating on first call."""
    global _verdict_thresholds_cache
    if _verdict_thresholds_cache is None:
        _verdict_thresholds_cache = VerdictThresholds.from_env()
    return _verdict_thresholds_cache


def reset_verdict_thresholds() -> None:
    """Clear the cached VerdictThresholds.

    Call this in test fixtures that set env vars for threshold tuning so the
    next ``_get_verdict_thresholds()`` call picks up the new values.
    """
    global _verdict_thresholds_cache
    _verdict_thresholds_cache = None


@dataclass(frozen=True)
class TreeSignals:
    node_count: int
    depth: int
    max_leaf_ratio: float
    flat_text: str
    garbled: bool
    garble_ratio: float
    effectively_garbled: bool
    is_reordered: bool
    expected_min_depth: int

    @classmethod
    def from_tree(
        cls,
        structure: list,
        expected_script: str | None = None,
        garble_threshold: float = 0.05,
    ) -> "TreeSignals":
        node_count = _tree_node_count(structure)
        depth = _tree_depth(structure)
        _, _, max_leaf_ratio = _tree_max_leaf_ratio(structure)
        flat_text = _flatten_tree_text(structure)
        garbled = _tree_is_garbled(structure, expected_script=expected_script)
        if garbled:
            gr = _garble_ratio(flat_text, expected_script=expected_script)
            effectively_garbled = gr >= garble_threshold
        else:
            gr = 0.0
            effectively_garbled = False
        is_reordered = _tree_is_reordered(structure)
        expected_min_depth = min(5, 2 + math.floor(math.log2(max(node_count, 1) / 50)))
        return cls(
            node_count=node_count,
            depth=depth,
            max_leaf_ratio=max_leaf_ratio,
            flat_text=flat_text,
            garbled=garbled,
            garble_ratio=gr,
            effectively_garbled=effectively_garbled,
            is_reordered=is_reordered,
            expected_min_depth=expected_min_depth,
        )


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
    """Concatenate all title+text from a tree structure into a single string.

    RFC-033 D1: parts are newline-separated so adjacent node boundaries cannot
    glue an Arabic title onto Latin text and fabricate a mixed-script pattern
    for `_has_sparse_mojibake`. Empty parts are dropped rather than joined —
    emitting a separator for an absent title would inflate the character counts
    that the volume floors in `classify_verdict` measure.
    """
    parts: list[str] = []

    def _walk(ns: list) -> None:
        for n in ns:
            for field in ("title", "text"):
                value = str(n.get(field, ""))
                if value:
                    parts.append(value)
            _walk(n.get("nodes") or [])

    _walk(nodes)
    return "\n".join(parts)


_LATIN_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")

_COMMON_WORDS: frozenset[str] = frozenset(
    {
        # English stopwords + common short words
        "the",
        "be",
        "to",
        "of",
        "and",
        "in",
        "that",
        "have",
        "it",
        "for",
        "not",
        "on",
        "with",
        "he",
        "as",
        "you",
        "do",
        "at",
        "this",
        "but",
        "his",
        "by",
        "from",
        "they",
        "we",
        "say",
        "her",
        "she",
        "or",
        "an",
        "will",
        "my",
        "one",
        "all",
        "would",
        "there",
        "their",
        "what",
        "so",
        "up",
        "out",
        "if",
        "about",
        "who",
        "get",
        "which",
        "go",
        "me",
        "when",
        "make",
        "can",
        "like",
        "time",
        "no",
        "just",
        "him",
        "know",
        "take",
        "people",
        "into",
        "year",
        "your",
        "good",
        "some",
        "could",
        "them",
        "see",
        "other",
        "than",
        "then",
        "now",
        "look",
        "only",
        "come",
        "its",
        "over",
        "think",
        "also",
        "back",
        "after",
        "use",
        "two",
        "how",
        "our",
        "work",
        "first",
        "well",
        "way",
        "even",
        "new",
        "want",
        "because",
        "any",
        "these",
        "give",
        "day",
        "most",
        "us",
        "is",
        "are",
        "was",
        "were",
        "been",
        "has",
        "had",
        "did",
        "does",
        "may",
        "must",
        "shall",
        "should",
        "might",
        "need",
        "very",
        "more",
        "much",
        "own",
        "such",
        "here",
        "where",
        "why",
        "each",
        "few",
        "both",
        "between",
        "under",
        "same",
        "still",
        "before",
        "through",
        "during",
        "without",
        "within",
        "per",
        "de",
        "re",
        # German stopwords
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einer",
        "einem",
        "einen",
        "eines",
        "und",
        "ist",
        "sind",
        "war",
        "hat",
        "mit",
        "auf",
        "für",
        "von",
        "aus",
        "bei",
        "nach",
        "zum",
        "zur",
        "sich",
        "nicht",
        "auch",
        "als",
        "nur",
        "noch",
        "oder",
        "aber",
        "wenn",
        "wird",
        "über",
        "ich",
        "wir",
        "sie",
        "man",
        "kann",
        "diese",
        "dieser",
        "diesem",
        "diesen",
        "dieses",
        "werden",
        "durch",
        "unter",
        "zwischen",
        "gegen",
        "ohne",
        "bis",
        "sein",
        "seine",
        "seinem",
        "seinen",
        "seiner",
        "ihre",
        "ihrem",
        "ihren",
        "ihrer",
        "mehr",
        "vor",
        "haben",
        "dass",
        "schon",
        "immer",
        "wieder",
        # Common technical/insurance terms that appear in bilingual docs
        "gmbh",
        "ag",
        "nr",
        "abs",
        "bzw",
        "etc",
        "max",
        "min",
        "pdf",
        "doc",
        "page",
        "file",
        "text",
        "data",
        "type",
        "article",
        "section",
        "paragraph",
        "clause",
        "item",
    }
)


def _latin_token_ratio(text: str) -> tuple[float, list[str]]:
    """Return (ratio_of_latin_tokens, latin_token_list) for garble scoring."""
    tokens = text.split()
    if not tokens:
        return 0.0, []
    latin_tokens = _LATIN_TOKEN_RE.findall(text)
    return len(latin_tokens) / len(tokens), latin_tokens


_VOWELS = frozenset("aeiouAEIOU")


def _is_morphologically_nonsense(token: str) -> bool:
    """Return True if a Latin token looks like garble rather than a real word.

    QF3 (RFC-021): hybrid morphological + whitelist approach.  The old
    pure-whitelist approach (~160 stopwords) mis-classified legitimate
    bilingual domain English as nonsense.  The fix:

    * **Hard failures** (always nonsense regardless of length):
      - digit-letter mixing ("xKjQ7", "mZpR3")
      - no vowels at all ("xkjqz", "vbwm")
    * **Long tokens (>=5 chars)** that survive the hard checks are treated
      as morphologically plausible domain words (e.g. "service",
      "infrastructure", "compliance") -- NOT nonsense.
    * **Short tokens (3-4 chars)** that survive the hard checks fall back
      to the ``_COMMON_WORDS`` whitelist.  This catches Tesseract
      syllable garble ("Bab", "rel", "teb") which has vowels but isn't
      a real word, while still passing common short words ("the", "for").
    * Tokens <=2 chars and short all-caps acronyms (<=5 chars) are exempt.
    """
    if len(token) <= 2:
        return False
    # All-caps short acronyms get a pass (SLA, PDF, HTTP, ...)
    if token.isupper() and len(token) <= 5:
        return False
    # Digits mixed with letters -> garble (e.g. "xKjQ7")
    has_alpha = False
    has_digit = False
    for c in token:
        if c.isalpha():
            has_alpha = True
        elif c.isdigit():
            has_digit = True
        if has_alpha and has_digit:
            return True
    # No vowels in a token of length >= 3 -> garble (e.g. "xkjqz", "vbwm")
    if not any(c in _VOWELS for c in token):
        return True
    # Long tokens with vowels are plausible domain words -> NOT nonsense
    if len(token) >= 5:
        return False
    # Short tokens (3-4 chars) with vowels: fall back to _COMMON_WORDS
    # whitelist.  Catches Tesseract syllable garble ("Bab", "rel", "teb")
    # while passing real short words ("the", "for", "can").
    return token.lower() not in _COMMON_WORDS


def garble_prongs(
    blob: str,
    *,
    expected_script: str | None = None,
    blob_kind: BlobKind = BlobKind.TREE_TEXT,
) -> frozenset[str]:
    """Return the set of garble-detection prongs that fired on *blob*.

    Each prong name corresponds to a specific heuristic check. An empty
    frozenset means no garbling detected. See ``_is_garbled_blob`` for the
    boolean wrapper used by all existing call sites.

    Zone-3: ``expected_script`` is keyword-only. ``blob_kind`` selects
    the normalization strategy via ``normalize_for_garble`` before
    ratio-based prongs evaluate.
    """
    prongs: set[str] = set()

    if not blob.strip():
        return frozenset({"empty"})

    # Zone-3: normalize before ratio computation
    norm = normalize_for_garble(blob, blob_kind)
    if not norm.strip():
        norm = blob  # fallback: normalization collapsed everything

    if "\x00" in blob or "\ufffd" in blob:
        prongs.add("null_replacement_bytes")
    if "GLYPH<" in blob:
        prongs.add("glyph_marker")

    bad = sum(1 for c in norm if ord(c) < 32 and c not in "\n\r\t")
    if (bad / max(len(norm), 1)) > 0.05:
        prongs.add("control_chars")

    pua = sum(1 for c in norm if 0xE000 <= ord(c) <= 0xF8FF)
    if (pua / max(len(norm), 1)) > 0.03:
        prongs.add("pua_chars")

    # Arabic Presentation-Forms ratio > 50% (RFC-028 D2)
    presentation_forms = sum(
        1 for c in norm if any(lo <= ord(c) <= hi for lo, hi in PRESENTATION_RANGES)
    )
    arabic_range_chars = sum(
        1 for c in norm if any(lo <= ord(c) <= hi for lo, hi in ARABIC_RANGES)
    )
    if arabic_range_chars > 0 and (presentation_forms / arabic_range_chars) > 0.50:
        prongs.add("presentation_forms")

    # Single-letter Arabic fragment ratio > 40% (D2 / RFC-033)
    arabic_tokens = [t for t in norm.split() if any(_is_arabic_char(c) for c in t)]
    if arabic_tokens:
        single_char_fragments = sum(1 for t in arabic_tokens if len(t) == 1 and t != "\u0648")
        if (single_char_fragments / len(arabic_tokens)) > 0.40:
            prongs.add("single_letter_fragments")

    # Digit ratio > 60% on blobs > GARBLE_DIGIT_FLOOR chars
    from .script import GARBLE_DIGIT_FLOOR

    if len(norm) > GARBLE_DIGIT_FLOOR:
        digits = sum(1 for c in norm if c.isdigit())
        if (digits / len(norm)) > 0.60:
            prongs.add("digit_ratio")

    # Single-token repetition > 30% (>20 alnum tokens)
    stripped = re.sub(r"<!--.*?-->", "", norm)
    tokens = [t for t in stripped.split() if any(c.isalnum() for c in t)]
    if len(tokens) > 20:
        most_common_count = Counter(tokens).most_common(1)[0][1]
        if (most_common_count / len(tokens)) > 0.30:
            prongs.add("token_repetition")

    # Latin-gibberish in non-Latin script context (D2 / RFC-019)
    if (
        expected_script
        and expected_script != "Latn"
        and os.environ.get("GARBLE_LATIN_GIBBERISH_ENABLED", "true").lower() != "false"
    ):
        latin_ratio_threshold = float(os.environ.get("GARBLE_LATIN_RATIO", "0.4"))
        nonsense_threshold = float(os.environ.get("GARBLE_NONSENSE_RATIO", "0.7"))
        ratio, latin_tokens = _latin_token_ratio(norm)
        if ratio > latin_ratio_threshold and len(latin_tokens) >= 5:
            nonsense = sum(1 for t in latin_tokens if _is_morphologically_nonsense(t))
            if nonsense / len(latin_tokens) > nonsense_threshold:
                prongs.add("latin_gibberish")

    return frozenset(prongs)


def _is_garbled_blob(
    blob: str,
    expected_script: str | None = None,
    blob_kind: BlobKind = BlobKind.TREE_TEXT,
) -> bool:
    """Boolean wrapper around ``garble_prongs`` -- True when any prong fires."""
    return bool(garble_prongs(blob, expected_script=expected_script, blob_kind=blob_kind))


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


# _JOINING_TYPE, _arabic_word_joins, _word_has_reversed_morphology moved to script.py
# (Zone 5: break circular import, dependency-free leaf)


def _check_bidi_coherence(text: str, n_samples: int = 5) -> tuple[bool, str]:
    """Zone-3: bidi-coherence check delegating to ``decide_rtl``.

    Previously a standalone 50-line sampling function; now delegates to
    the consolidated decider. The ``decide_rtl`` ``reversed`` flag
    subsumes the old visual-order-garble detection.

    Returns:
        (True, "")                      - bidi-coherent (or not Arabic-dominant)
        (False, "visual_order_garble")  - reversed detected
    """
    decision: RtlDecision = decide_rtl(text, sample_count=n_samples)
    if decision.reversed:
        return False, "visual_order_garble"
    return True, ""


_GARBLE_NODE_RATIO_THRESHOLD_RAW = float(os.getenv("GARBLE_NODE_RATIO_THRESHOLD", "0.10"))
_GARBLE_NODE_RATIO_THRESHOLD = (
    _GARBLE_NODE_RATIO_THRESHOLD_RAW if 0 <= _GARBLE_NODE_RATIO_THRESHOLD_RAW <= 1 else 0.10
)
# RFC-029 D10: zero-body contamination gate — fraction of non-root nodes whose
# stripped body text is empty.  Threshold is env-overridable for calibration.
_EMPTY_NODE_FRACTION_THRESHOLD_RAW = 0.30
_EMPTY_NODE_FRACTION_THRESHOLD = float(
    os.environ.get("EMPTY_NODE_FRACTION_THRESHOLD", str(_EMPTY_NODE_FRACTION_THRESHOLD_RAW))
)
# RFC-029 D1 (Task 3.1): flat-prefer multiplier — when flat char count exceeds
# tree char count by this factor, prefer the flat result over the tree result.
_RFC029_FLAT_PREFER_MULTIPLIER = float(os.environ.get("RFC029_FLAT_PREFER_MULTIPLIER", "3.0"))
# RFC-029 D1 (Task 3.1): minimum chars-per-node floor; trees below this floor
# (with enough nodes to make the metric meaningful) fail with low_content_density.
_RFC029_MIN_CHARS_PER_NODE = float(os.environ.get("RFC029_MIN_CHARS_PER_NODE", "150"))
# RFC-029 D2 (Task 3.3): minimum chars-per-page floor for scanned density check;
# trees below this floor (when page_count is known) fail with suspect_density.
_RFC029_MIN_SCANNED_DENSITY_FLOOR = float(
    os.environ.get("RFC029_MIN_SCANNED_DENSITY_FLOOR", "1500")
)


def _infer_script(text: str) -> str | None:
    """Infer the dominant Unicode script of text by character-block majority.

    Returns 'Arab', 'Latn', or None if ambiguous/empty.
    Thin wrapper over script.infer_script preserving the original guards:
    short-text (< 10 chars) and low-signal (< 5 script chars) return None,
    and extended Latin (U+00C0-U+024F) is counted as Latin.
    """
    if len(text.strip()) < 10:
        return None
    arab_count = 0
    latn_count = 0
    for ch in text:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in ARABIC_RANGES):
            arab_count += 1
        elif (0x0041 <= cp <= 0x005A) or (0x0061 <= cp <= 0x007A) or (0x00C0 <= cp <= 0x024F):
            latn_count += 1
    total = arab_count + latn_count
    if total < 5:
        return None
    if arab_count / total > 0.5:
        return "Arab"
    if latn_count / total > 0.5:
        return "Latn"
    return None


def _script_from_filename(filename: str) -> str | None:
    """Derive expected Unicode script from filename via OCR-language detection.

    Returns ``"Arab"`` when the filename signals Arabic content, else ``None``.
    """
    from .converters import detect_ocr_langs  # late import avoids adding a top-level dep

    langs = detect_ocr_langs(filename)
    if "ara" in langs:
        return "Arab"
    return None


def _garble_check_nodes(
    nodes: list[dict], page_script: str | None = None, expected_script: str | None = None
) -> int:
    """Recursively count nodes whose text or title is individually garbled."""
    garbled = 0
    for node in nodes:
        node_garbled = False
        text = node.get("text") or ""
        if text.strip():
            if expected_script is not None:
                # QF3 (RFC-021): when text-inferred script disagrees with
                # filename-derived expected_script, use the INFERRED script
                # for this node.  Previously expected_script always won,
                # causing English-only nodes in bilingual docs to be checked
                # as Arabic, which false-flagged legitimate English text.
                inferred = _infer_script(text) if len(text) >= 50 else None
                if inferred is not None and inferred != expected_script:
                    logger.warning(
                        "Script mismatch: filename-derived=%s, text-inferred=%s "
                        "(using text-inferred for this node)",
                        expected_script,
                        inferred,
                    )
                    node_script = inferred
                else:
                    node_script = expected_script
            else:
                node_script = _infer_script(text) if len(text) >= 50 else page_script
            if _is_garbled_blob(text, expected_script=node_script):
                node_garbled = True
        # RFC-030 D4: titles carry user-visible content too (23/24 reversed
        # RTL titles in siyasat-hawkama were invisible to this gate). Titles
        # are short (10-100 chars), so a per-word reversed-morphology check
        # catches RTL-reversal without tripping the bulk-ratio heuristics
        # (digit/repetition ratios only kick in on longer blobs) that would
        # false-positive on short legitimate mixed-script titles.
        title = node.get("title") or ""
        if title.strip() and (
            any(_word_has_reversed_morphology(w) for w in title.split())
            or _is_garbled_blob(title, expected_script=expected_script or page_script)
        ):
            node_garbled = True
        if node_garbled:
            garbled += 1
        children = node.get("nodes") or []
        garbled += _garble_check_nodes(
            children, page_script=page_script, expected_script=expected_script
        )
    return garbled


def _tree_is_garbled(nodes: list, expected_script: str | None = None) -> bool:
    if not nodes:
        return False
    blob = _flatten_tree_text(nodes)
    # Additive OR (RFC-015 D8): existing bulk heuristics first, then sparse
    # mixed-script. Never narrows the existing gate.
    return _is_garbled_blob(blob, expected_script=expected_script) or _has_sparse_mojibake(blob)


def _tree_is_rtl_reversed(nodes: list) -> bool:
    """Zone-3: thin shim delegating to ``decide_rtl`` from script.py.

    Previously a standalone 50-line sampling function duplicating the
    RTL detection logic; now delegates to the consolidated decider.
    Kept as a named function so the ``validate_tree`` call site remains
    readable (the boolean sense matches the gate expectation).
    """
    if not nodes:
        return False
    full_text = _flatten_tree_text(nodes)
    if not full_text:
        return False
    decision: RtlDecision = decide_rtl(full_text)
    return decision.reversed


def validate_tree(  # noqa: C901
    structure: list,
    expected_script: str | None = None,
    page_count: int | None = None,
) -> TreeGateResult:
    """Gate a PageIndex tree before persistence (HR5 / WORKER-01-C2).

    Returns a ``TreeGateResult`` (iterable as ``(ok, reason_str)`` for
    backward-compat tuple unpacking).  The result carries a ``signals``
    field with the :class:`TreeSignals` computed during gating so that
    ``classify_verdict`` can consume them without re-derivation.

    Fails (priority order) on garbling, node_count < 3, depth < 2,
    node_garbling, reordered, rtl_reversal, bidi_degraded,
    empty_node_contamination, low_content_density, suspect_density.

    Gate 11 (arabic_low_content_ratio) was removed: it is a strict subset
    of gate 1 (_tree_is_garbled already tests _is_garbled_blob on the
    flattened text) and was unreachable.
    """
    # Compute TreeSignals ONCE and attach to every returned TreeGateResult
    # so that classify_verdict can consume them without re-derivation.
    th = _get_verdict_thresholds()
    sig = TreeSignals.from_tree(
        structure,
        expected_script=expected_script,
        garble_threshold=th.garble_threshold,
    )

    if sig.garbled:
        return TreeGateResult(False, TreeDefect.GARBLING, signals=sig)
    if sig.node_count < 3:
        return TreeGateResult(False, TreeDefect.NODE_COUNT_LOW, signals=sig)
    if sig.depth < 2:
        return TreeGateResult(False, TreeDefect.DEPTH_LOW, signals=sig)
    # RFC-018 D3b: per-node garble ratio — catches documents where a minority of
    # nodes are garbled but the flattened full-text dilutes below the bulk gate.
    doc_script = _infer_script(sig.flat_text)
    if (
        sig.node_count > 0
        and (
            _garble_check_nodes(structure, page_script=doc_script, expected_script=expected_script)
            / sig.node_count
        )
        > _GARBLE_NODE_RATIO_THRESHOLD
    ):
        return TreeGateResult(False, TreeDefect.NODE_GARBLING, signals=sig)
    # RFC-015 D2 (HR5 tightening): reject content-ordering regressions. A caller
    # surfaces this reason as a low_quality_tree error rather than persisting.
    if sig.is_reordered:
        return TreeGateResult(False, TreeDefect.REORDERED, signals=sig)
    # RFC-027 D3: additive prong — correctly-encoded but reversed Arabic text
    # passes every check above (no garbling, real node/depth counts, in-order
    # start_indexes) but reads backwards. Checked last so it never shadows the
    # existing garble/structure gates it is additive to.
    if _tree_is_rtl_reversed(structure):
        return TreeGateResult(False, TreeDefect.RTL_REVERSAL, signals=sig)
    # RFC-030 D5 / RFC-033 D2 Part B: bidi coherence gate — catches
    # visual-order Arabic (reversed morphology) that _tree_is_rtl_reversed
    # does not detect. BIDI_COHERENCE_ENFORCE now defaults to true, promoted
    # on Task 9.1's scoped re-ingest measurement of `bidi_coherence_violations`
    # (docs with reversed-heading signatures, post the Task 1.11 heading
    # guard) -- that measurement is a LOWER BOUND on the clean-doc
    # false-positive rate, not a corpus-wide estimate, since the sample was
    # drawn from the population already known to be affected. It is not yet
    # tight enough to justify persistence-gating (RFC-033 D2 requires <2% FP
    # across a full corpus cycle for that), so enforcement here is
    # verdict-only: set `bidi_degraded` (surfaced as the "bidi_degraded"
    # validate_reason) instead of raising LowQualityTreeError.
    # classify_verdict caps the returned verdict at MARGINAL when it sees
    # this reason; persistence is never gated on it.
    _bidi_ok, _bidi_reason = _check_bidi_coherence(sig.flat_text)
    if not _bidi_ok:
        if os.environ.get("BIDI_COHERENCE_ENFORCE", "true").lower() == "true":
            logger.warning(
                "validate_tree: bidi coherence check failed (%s); setting "
                "bidi_degraded (verdict-only, not persistence-gating)",
                _bidi_reason,
            )
            return TreeGateResult(False, TreeDefect.BIDI_DEGRADED, signals=sig)
        logger.warning(
            "validate_tree: bidi coherence check would fail (%s) but "
            "BIDI_COHERENCE_ENFORCE is disabled (audit-only mode); not gating",
            _bidi_reason,
        )
    # RFC-029 D10: zero-body contamination gate — checked last so it is
    # additive and never shadows the existing gates above.
    _total_non_root, _empty_leaf, _empty_non_leaf = _count_empty_body_nodes(structure)
    if _total_non_root > 0:
        _empty_fraction = (_empty_leaf + _empty_non_leaf) / _total_non_root
        if _empty_fraction > _EMPTY_NODE_FRACTION_THRESHOLD:
            return TreeGateResult(
                False,
                TreeDefect.EMPTY_NODE_CONTAMINATION,
                f"fraction={_empty_fraction:.2f}"
                f",empty_leaf={_empty_leaf}"
                f",empty_non_leaf={_empty_non_leaf}"
                f",total_non_root={_total_non_root}",
                signals=sig,
            )
    # RFC-029 D1 (Task 3.1): content-density gate — only when node count >= 200.
    # The target failure mode is scanned-garble trees with many hundreds of shell
    # nodes, each carrying near-zero extracted text (e.g. 2 chars/node over 300
    # nodes = 600 chars total for a multi-article law — clearly a failed extraction).
    # Setting the floor at 200 nodes avoids false-positives on real compact documents
    # and synthetic test fixtures (which never approach that node count).
    # The node_count<3 / depth<2 gates above already handle truly degenerate trees.
    if sig.node_count >= 200:
        chars_per_node = len(sig.flat_text) / sig.node_count
        if chars_per_node < _RFC029_MIN_CHARS_PER_NODE:
            return TreeGateResult(
                False,
                TreeDefect.LOW_CONTENT_DENSITY,
                f"chars_per_node={chars_per_node:.1f}"
                f",threshold={_RFC029_MIN_CHARS_PER_NODE:.1f}",
                signals=sig,
            )
    # RFC-029 D2 (Task 3.3): scanned-density floor — only when page_count is
    # provided and positive (guard against zero-page edge-cases).
    if page_count is not None and page_count > 0:
        chars_per_page = len(sig.flat_text) / page_count
        if chars_per_page < _RFC029_MIN_SCANNED_DENSITY_FLOOR:
            return TreeGateResult(
                False,
                TreeDefect.SUSPECT_DENSITY,
                f"chars_per_page={chars_per_page:.1f}",
                signals=sig,
            )
    # NOTE: Gate 11 (arabic_low_content_ratio) was here but has been removed.
    # It tested _is_garbled_blob(full_text) which is a strict subset of gate 1
    # (_tree_is_garbled already calls _is_garbled_blob on the flattened text).
    # Gate 1 fires first when the blob is garbled, making gate 11 unreachable.
    # TreeDefect.ARABIC_LOW_CONTENT_RATIO is kept in the StrEnum and
    # REASON_POLICY for backward-compat with persisted verdict_reason strings.
    return TreeGateResult(True, TreeDefect.OK, signals=sig)


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


def _count_empty_body_nodes(structure: list) -> tuple[int, int, int]:
    """RFC-029 D10: count non-root nodes with empty stripped body text.

    Returns (total_non_root, empty_leaf, empty_non_leaf).
    A node's «body text» is its ``text`` field (stripped); ``title``-only
    nodes are intentional structural nodes and are NOT counted as empty.
    Counts are over all non-root nodes (the entire tree minus the top-level
    list elements, which are document roots).
    """
    total = 0
    empty_leaf = 0
    empty_non_leaf = 0

    def _walk(nodes: list, is_root_level: bool = False) -> None:
        nonlocal total, empty_leaf, empty_non_leaf
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            children = node.get("nodes") or []
            if not is_root_level:
                total += 1
                body = node.get("text", "") or ""
                if not body.strip() and not str(node.get("title") or "").strip():
                    if children:
                        empty_non_leaf += 1
                    else:
                        empty_leaf += 1
            _walk(children, is_root_level=False)

    _walk(structure, is_root_level=True)
    return total, empty_leaf, empty_non_leaf


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


def _garble_ratio(text, expected_script=None):
    """Windowed garble ratio: fraction of fixed-size windows that individually
    trigger garble detection. RFC-033 D1: no longer re-checks the full text
    (that duplicates what _tree_is_garbled already gates in classify_verdict)."""
    window = 2000
    if len(text) <= window:
        return (
            1.0
            if (
                _is_garbled_blob(text, expected_script=expected_script)
                or _has_sparse_mojibake(text)
            )
            else 0.0
        )
    chunks = [text[i : i + window] for i in range(0, len(text), window)]
    garbled_chunks = sum(
        1
        for c in chunks
        if _is_garbled_blob(c, expected_script=expected_script) or _has_sparse_mojibake(c)
    )
    return garbled_chunks / len(chunks)


def compute_image_enrichment_ratio(image_blocks: list[dict]) -> float | None:
    """RFC-036 D4: excludes intentionally-skipped blocks (``decorative`` is
    True, or ``skipped_reason`` truthy -- e.g. decorative-icon filter,
    OCR min-chars gate, page_coverage threshold, clip_text_already_exported,
    or D0's landscape_fallback_picture) from both the enriched numerator
    and the total denominator, so correctly-skipped picture regions never
    count as unenriched gaps toward classify_verdict's image_enrichment_promoted
    path."""
    scored_blocks = [
        b for b in image_blocks if not b.get("decorative") and not b.get("skipped_reason")
    ]
    if not scored_blocks:
        return None
    enriched_count = sum(
        1
        for b in scored_blocks
        if b.get("ocr_text") or b.get("description") or b.get("figure_path")
    )
    return enriched_count / len(scored_blocks)


def _classify_image_verdict(image_enrichment_ratio: float | None) -> tuple[str, str]:
    """Verdict for image-standalone documents."""
    if image_enrichment_ratio is not None and image_enrichment_ratio >= 0.8:
        return "PASS", "image_enrichment_complete"
    if image_enrichment_ratio is not None and image_enrichment_ratio > 0:
        return "MARGINAL", f"image_enrichment_partial(ratio={image_enrichment_ratio:.2f})"
    return "FAIL", "no_image_enrichment"


def _dedupe_chart_text_lines(text: str) -> str:
    """RFC-027 D1: drop repeated '> [Chart text]:' lines before a promoted
    doc's char/garble calculations -- a single OCR read spliced into prose
    can otherwise be double-counted toward both the char floor and the
    garble check. Keeps the first occurrence of each distinct line. Pure."""
    seen: set[str] = set()
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        if _FLAT_CHART_TEXT_RE.match(line.strip()):
            if line in seen:
                continue
            seen.add(line)
        kept.append(line)
    return "".join(kept)


def _defect_from_reason_str(reason: str | None) -> TreeDefect:
    """Parse a validate_reason string back into a :class:`TreeDefect`.

    Handles both exact matches (``"garbling"``) and prefix matches with
    parenthesised detail (``"empty_node_contamination(fraction=0.35,...)"``)
    by matching against each ``TreeDefect.value``.
    """
    if not reason:
        return TreeDefect.OK
    for td in TreeDefect:
        if td.value and (reason == td.value or reason.startswith(td.value + "(")):
            return td
    return TreeDefect.OK


def classify_verdict(  # noqa: C901
    structure: list,
    content_class: str,
    validate_result: TreeGateResult | str | None,
    image_enrichment_ratio: float | None = None,
    prior_verdict: str | None = None,
    inspector_class: str | None = None,
    expected_script: str | None = None,
) -> tuple[str, str]:
    """Grouped-rule verdict engine.

    GROUP 1 -- HARD_FAILs: any defect in :data:`HARD_FAIL_DEFECTS` returns
               FAIL immediately (takes priority over all content-class
               dispatch, including image_standalone).  Additionally,
               ``sig.is_reordered`` acts as an independent fallback.
    DISPATCH - image_standalone: own verdict logic after hard-fails clear.
    GROUP 2 -- PROMOTIONS: image-enrichment rescue, base PASS, category
               promotions (cat_a/b/c), small-doc exemption, MARGINAL fallback.
    CAPS    -- bidi_degraded and depth-adequacy applied uniformly via _pass()
               to every PASS-returning branch in Group 2.

    ``validate_result`` accepts a :class:`TreeGateResult` (preferred) which
    carries both the defect enum and pre-computed :class:`TreeSignals`,
    eliminating redundant re-derivation.  A plain ``str`` (the legacy
    ``validate_reason`` format) is still accepted for backward compatibility
    with callers that have not yet been updated (e.g. promotion_sweep.py,
    preprocess_client.py).

    The image-enrichment rescue is intentionally positioned before the
    max_leaf_ratio structural hard-fail: flat image-enriched documents render
    as a single leaf (max_leaf_ratio=1.0), so the structural metric is not
    meaningful for them.  This ordering is locked by RFC-022 B2.  Genuine
    image-only documents should be routed upstream to content_class=
    'image_standalone' (GROUP 0), which is already exempt.
    """
    # ── Pre-compute thresholds (cached per-process) ──────────────────────
    th = _get_verdict_thresholds()

    # Zero-content fast path (before signals, which need non-empty tree)
    if _tree_node_count(structure) == 0 or len(_flatten_tree_text(structure).strip()) == 0:
        return "FAIL", "zero_content"

    # ── Normalize validate_result into (defect, validate_reason, signals) ──
    if isinstance(validate_result, TreeGateResult):
        defect = validate_result.defect
        validate_reason: str | None = str(validate_result) if validate_result.defect != TreeDefect.OK else None
        sig = validate_result.signals
    elif isinstance(validate_result, str):
        validate_reason = validate_result
        defect = _defect_from_reason_str(validate_result)
        sig = None
    else:
        validate_reason = None
        defect = TreeDefect.OK
        sig = None

    # Compute signals if not provided by TreeGateResult
    if sig is None:
        sig = TreeSignals.from_tree(structure, expected_script=expected_script, garble_threshold=th.garble_threshold)

    # ── GROUP 1: HARD_FAILs (any one is terminal) ────────────────────────
    # Hard-fails take priority over all content-class dispatch, including
    # image_standalone (a garbled image doc still FAILs).
    if defect in HARD_FAIL_DEFECTS:
        return "FAIL", validate_reason or defect.value
    if sig.is_reordered:
        return "FAIL", "reordered"

    # ── Content-class dispatch: image_standalone ─────────────────────────
    # image_standalone has its own verdict logic; tree-shape metrics are
    # structurally meaningless for single-image documents.  Placed after
    # GROUP 1 so garbling/reordered still hard-fail image docs.
    if content_class == "image_standalone":
        return _classify_image_verdict(image_enrichment_ratio)

    # ── CAPS: shared _pass() wrapper ─────────────────────────────────────
    # Every Group-2 branch returning PASS funnels through _pass(), which
    # applies two uniform caps:
    #   1. bidi_degraded -> MARGINAL (RFC-018 D2)
    #   2. depth-adequacy -> MARGINAL when the tree is shallower than its
    #      node count warrants (RFC-036 D6).  Previously only applied in
    #      the base-PASS branch; now uniform across all promotions.
    _bidi_degraded = defect == TreeDefect.BIDI_DEGRADED

    def _pass(reason: str) -> tuple[str, str]:
        if _bidi_degraded:
            return "MARGINAL", "bidi_degraded"
        if sig.depth < sig.expected_min_depth and not sig.effectively_garbled:
            return (
                "MARGINAL",
                f"depth_inadequate:expected_min_depth={sig.expected_min_depth},actual_depth={sig.depth}",
            )
        return "PASS", reason

    # ── GROUP 2: PROMOTIONS (tried only when no HARD_FAIL fired) ─────────

    # 2b: image-enrichment rescue (RFC-022 B2) — intentionally before
    # max_leaf_ratio hard-fail; see docstring for rationale.
    if (
        content_class in ("flat_prose", "flat_mixed")
        and image_enrichment_ratio is not None
        and image_enrichment_ratio >= 0.8
    ):
        _promoted_text = _dedupe_chart_text_lines(sig.flat_text)
        total_chars = len(_promoted_text)
        if total_chars < th.min_image_promoted_chars:
            return "MARGINAL", "image_enrichment_promoted_below_char_floor"
        if not _is_garbled_blob(_promoted_text):
            return _pass("image_enrichment_promoted")

    # max_leaf_ratio structural hard FAIL — blocks all non-image promotions
    # below.  Image-enrichment rescue above may bypass this for flat docs
    # with high enrichment ratios (RFC-022 B2 explicit exception).
    if sig.max_leaf_ratio > th.hard_fail_max_leaf_ratio:
        return "FAIL", f"max_leaf_ratio={sig.max_leaf_ratio:.2f}"

    # 2c: base PASS (hysteresis widens threshold for previously-PASS docs)
    _effective_max_leaf = th.pass_max_leaf_ratio
    if prior_verdict == "PASS":
        _effective_max_leaf = th.pass_max_leaf_ratio + th.hysteresis_band
    if (
        sig.node_count >= 3
        and sig.depth >= 2
        and sig.max_leaf_ratio < _effective_max_leaf
        and not sig.effectively_garbled
    ):
        return _pass("")

    # 2d-2f: category-specific promotions
    if content_class.startswith("ocr_"):
        if sig.max_leaf_ratio < 0.15 and ocr_noise_ratio(sig.flat_text) < 0.005:
            return _pass("cat_a_promoted")
    elif content_class.startswith("flat_"):
        _stripped_flat_text = sig.flat_text.strip()
        _text_blocks = [b for b in _stripped_flat_text.splitlines() if b.strip()]
        _placeholder_ratio = (
            sum(1 for b in _text_blocks if b.strip() == "<!-- image -->") / len(_text_blocks)
            if _text_blocks
            else 0.0
        )
        if (
            not sig.effectively_garbled
            and sig.max_leaf_ratio < th.cat_bc_promotion_threshold
            and sig.node_count >= 3
            and len(_stripped_flat_text) >= th.min_flat_promotion_chars
            and _placeholder_ratio <= 0.5
        ):
            return _pass("cat_b_promoted")
    else:
        _cat_c_threshold = th.cat_bc_promotion_threshold
        if not content_class and inspector_class == "text_based":
            _cat_c_threshold = th.cat_bc_promotion_threshold * 1.2
        if (
            not sig.effectively_garbled
            and hash_pipe_ratio(sig.flat_text) < 0.01
            and sig.max_leaf_ratio < _cat_c_threshold
        ):
            return _pass("cat_c_promoted")

    # 2g: small-doc exemption (flat_ only)
    _small_doc_leaf_ratio_bound = (
        th.small_doc_leaf_ratio_bound_high if sig.node_count <= 5 else th.small_doc_leaf_ratio_bound_low
    )
    if (
        th.small_doc_enabled
        and not sig.effectively_garbled
        and content_class.startswith("flat_")
        and sig.node_count >= 1
        and sig.node_count <= 10
        and sig.max_leaf_ratio < _small_doc_leaf_ratio_bound
        and 100 <= len(sig.flat_text.strip()) < 15000
    ):
        return _pass("small_doc_promoted")

    # ── MARGINAL fallback ────────────────────────────────────────────────
    if sig.effectively_garbled:
        reason = f"garbling(ratio={sig.garble_ratio:.2f})"
    elif sig.node_count < 3:
        reason = f"node_count={sig.node_count}"
    elif sig.depth < 2:
        reason = f"depth={sig.depth}"
    else:
        reason = f"leaf_concentration={sig.max_leaf_ratio:.2f}"
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
# unresolved marker left by splice_figure_markers (RFC-023 D1): no matching
# PictureResult, so it never became a `[Figure: fig-N]` reference.
_FLAT_RAW_IMAGE_RE = re.compile(r"^<!--\s*image\s*-->$")
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
    # RFC-024 D3: MOU/decree markers (Clause/Part/Annex + بند/باب)
    r"|Clause\s+\(?\s*(?P<clause>\d+(?:\.\d+)?)"  # Clause 4 / Clause (4)
    r"|Part\s+\(?\s*(?P<part>(?:[IVX]+|\d+)(?:\.\d+)?)"  # Part IV / Part 4
    r"|بند\s*\(?\s*(?P<band>[\d٠-٩]+(?:[.٫][\d٠-٩]+)?)"  # بند (5) / بند ٥
    r"|باب\s*\(?\s*(?P<bab>[\d٠-٩]+(?:[.٫][\d٠-٩]+)?)"  # باب (5) / باب ٥
    r"|Annex\s+\(?\s*(?P<annex>[A-Z]|\d+(?:\.\d+)?)"  # Annex A / Annex 4
    # RFC-028 D7: standalone Roman-numeral sub-clause markers ("I. ", "II. ").
    # Gated on ≥2 matches per leaf in split_oversized_leaf_nodes below, since a
    # single incidental "I." in prose is not a heading.
    r"|(?P<roman>[IVX]+)\.\s"
    r")",
    re.IGNORECASE,
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


def _roman_to_int(s: str) -> int:
    """Convert an uppercase Roman numeral (``I``-``XXXIX``, per RFC-024 D3's
    ``Part`` marker) to an int. No large-numeral subtractive pairs (``CM``,
    ``CD``, …) are needed at this range."""
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


def _ordinal_value(m: "re.Match[str]") -> tuple[int, ...]:
    """The ordinal captured by whichever marker alternative matched, as a tuple of
    dotted components compared lexicographically (NOT a float — ``3.10`` must
    stay distinct from ``3.1``, whereas ``float("3.10") == float("3.1")`` would
    silently collapse them and eject a genuine heading from the increasing run).

    RFC-024 D3: ``part`` and ``annex`` can carry non-decimal tokens (Roman
    numerals, bare Latin letters) that ``int()`` cannot parse — ``part`` is
    converted per dotted component (Roman or decimal each), ``annex`` tries
    decimal first and falls back to letter ordinals on ``ValueError``."""
    part = m.group("part")
    if part is not None:
        # Convert per dotted component: the pattern permits a Roman head with a
        # decimal suffix ("Part IV.2"), so a whole-token _roman_to_int fallback
        # would KeyError on the "." / digit characters.
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


def _split_on_blank_line_paragraphs(
    node: dict,
    text: str,
    max_chars: int,
    min_segments: int,
    min_seg_chars: int = 2000,
) -> bool:
    """RFC-024 D3 (Task 2.3): last-resort fallback for leaves where neither the
    ordinal splitter nor the فقرة marker fallback (``_split_on_paragraph_markers``)
    found a structural sequence — OCR-recovered text with no ATX headings and no
    ordinal markers at all. Splits on blank-line-separated paragraph boundaries.
    Same minimum-inter-segment-chars floor and every-segment-under-max_chars
    acceptance guard as the فقرة fallback, so a leaf is left untouched rather
    than half-split."""
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
    eligible for splitting. RFC-024 D3: also recognizes Clause/Part/Annex/بند/باب
    MOU/decree markers, since they are alternatives on the same compiled regex."""
    if not text:
        return False
    folded, _ = _fold_with_index_map(text)
    return _OVERSIZED_ORDINAL_RE.search(folded) is not None


def _blank_line_fallback_enabled(tree_ratio: float) -> bool:
    """RFC-024 D3 (Task 2.3): gate for the blank-line paragraph-boundary
    fallback.  Uses its own ``LEAF_SPLIT_RATIO`` env var (defaulting to the
    current ``PASS_MAX_LEAF_RATIO`` value) so that tuning the scoring
    threshold does not change the tree shape that produces the metric
    being scored (Zone-2 feedback-loop fix)."""
    enabled = os.environ.get("LEAF_CONCENTRATION_PARAGRAPH_SPLIT_ENABLED", "true")
    if enabled.strip().lower() in {"false", "0", "no", "off"}:
        return False
    default = os.environ.get("PASS_MAX_LEAF_RATIO", "0.30")
    leaf_split_ratio = float(os.environ.get("LEAF_SPLIT_RATIO", default))
    return tree_ratio > leaf_split_ratio


def split_oversized_leaf_nodes(
    structure: list,
    max_chars: int = 50000,
    min_segments: int = 3,
    _tree_ratio: float | None = None,
    _tree_total: int | None = None,
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
    under ``max_chars`` so a second pass is a no-op.

    RFC-024 D3 (Task 2.3): ``_tree_ratio`` / ``_tree_total`` are the whole-tree
    ``max_leaf_ratio`` and total leaf chars (``_tree_max_leaf_ratio``), computed
    once on the top-level call and threaded through recursion so they always
    reflect the original tree, not a subtree. They gate the blank-line
    paragraph-boundary fallback for leaves where the ordinal splitter and the
    فقرة marker fallback both find no structural sequence at all — including
    marker-less leaves UNDER ``max_chars`` whose own share of the tree's leaf
    chars exceeds ``PASS_MAX_LEAF_RATIO`` (the "even under 50k chars" case in
    RFC-024 D3 item 3)."""
    if _tree_ratio is None:
        _, _tree_total, _tree_ratio = _tree_max_leaf_ratio(structure)

    for node in structure or []:
        if not isinstance(node, dict):
            continue
        children = node.get("nodes")
        if children:
            # Parent node: recurse, leave its own text untouched.
            split_oversized_leaf_nodes(children, max_chars, min_segments, _tree_ratio, _tree_total)
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
            # RFC-024 D3 (Task 2.3): a marker-less leaf under max_chars is still
            # split-eligible when IT ALONE holds more than PASS_MAX_LEAF_RATIO of
            # the tree's leaf chars (the "even under 50k chars" case — OCR text
            # with no ATX headings and no ordinal markers). Per-leaf share >
            # threshold implies the whole-tree max_leaf_ratio exceeds it too
            # (the tree ratio is the max over leaves), so this stays strictly
            # within the RFC's tree-level trigger while never fragmenting small
            # leaves that are not the concentration culprit.
            leaf_share = (
                (len(node.get("title", "")) + len(text)) / _tree_total if _tree_total else 0.0
            )
            if not _blank_line_fallback_enabled(leaf_share):
                continue

        folded, idx_map = _fold_with_index_map(text)
        all_matches = list(_OVERSIZED_ORDINAL_RE.finditer(folded))

        # RFC-028 D7: a lone Roman-numeral marker ("I. ") is not distinguishable
        # from incidental prose ("I. went to the store"); require ≥2 matches of
        # this alternative in the leaf before letting it feed the split decision.
        roman_idx = {i for i, m in enumerate(all_matches) if m.group("roman") is not None}
        if 0 < len(roman_idx) < 2:
            all_matches = [m for i, m in enumerate(all_matches) if i not in roman_idx]

        # Cover/bibliography/ToC blocks (dotted leaders, ~no ordinal markers):
        # accept as-is rather than force-splitting a bibliography on فقرة.
        if _looks_like_frontmatter_toc(text, all_matches):
            continue

        if len(all_matches) < min_segments:
            if _split_on_paragraph_markers(node, text, max_chars, min_segments) or (
                _blank_line_fallback_enabled(_tree_ratio)
                and _split_on_blank_line_paragraphs(node, text, max_chars, min_segments)
            ):
                split_oversized_leaf_nodes(
                    node["nodes"], max_chars, min_segments, _tree_ratio, _tree_total
                )
            continue

        # Keep only the longest strictly-increasing ordinal run (drops cross-refs).
        values = [_ordinal_value(m) for m in all_matches]
        keep_idx = _longest_increasing_run(values)
        if len(keep_idx) < min_segments:
            # مادة/Article markers exist but don't form a long enough increasing
            # run (e.g. RTL reading-order scramble) — fall back to فقرة.
            if _split_on_paragraph_markers(node, text, max_chars, min_segments) or (
                _blank_line_fallback_enabled(_tree_ratio)
                and _split_on_blank_line_paragraphs(node, text, max_chars, min_segments)
            ):
                split_oversized_leaf_nodes(
                    node["nodes"], max_chars, min_segments, _tree_ratio, _tree_total
                )
            continue
        # Map kept markers back to ORIGINAL text start offsets, in order.
        starts = [idx_map[all_matches[k].start()] for k in keep_idx]

        _apply_split(node, text, starts)
        # Recurse into the new children: a single article that is itself oversized
        # (sub-clauses, or a gap whose inner markers were not part of the top-level
        # increasing run) gets a second split pass. Terminates because each pass
        # strictly shrinks segments.
        split_oversized_leaf_nodes(node["nodes"], max_chars, min_segments, _tree_ratio, _tree_total)

    return structure


def _segment_table_nodes(structure: list) -> list:  # noqa: C901, PLR0915
    """RFC-029 D7 (Task 5.3, Property 9) — table-aware node segmentation.

    Walks an already-built ``structure`` (post heading-node construction, pre
    ``validate_tree``) and splits any node whose body exceeds
    ``_RFC029_TABLE_SEGMENT_CHAR_THRESHOLD`` chars AND contains a pipe-table
    with more than ``_RFC029_TABLE_SEGMENT_MIN_ROWS`` data rows.

    Interaction risk guard (per tasks-file Note): only nodes above the char
    threshold are touched — avoids fragmenting already-thin trees that D1 may
    route to flat.

    RFC-036 D0 singleton-ratio guard: a candidate table is only segmented out
    if <= 60% of its data rows are single-value cells (chart axis labels).
    Above that ratio the table is chart content, not a real multi-column
    table, and segmenting it would explode into dozens of singleton kv nodes
    — the block is left intact instead.

    Split contract (content-preservation invariant): the concatenated child
    body text equals the original node text when joined with a single newline.
    Edge cases handled:
      - Table at start of node: prose portion is empty; only the table child is
        created, heading is inherited from parent.
      - Multiple tables in one node: each table becomes a separate child; any
        prose between tables is a prose child.
      - Table with no header row: synthesized heading is ``Table: {parent title}``.

    Mutates ``structure`` in place and returns it.  Idempotent: segments that
    fall under the char threshold are skipped on a second pass.

    Pure Python, stdlib only.  No LLM, no MinIO/Redis/VLM call.
    """
    _SEP_RE = re.compile(r"^\|[\s|:-]+\|$")  # separator row: |---|---|
    _PIPE_START = "|"

    def _is_pipe_row(line: str) -> bool:
        s = line.strip()
        return s.startswith(_PIPE_START) and s.endswith(_PIPE_START) and len(s) > 1

    def _is_sep_row(line: str) -> bool:
        return bool(_SEP_RE.match(line.strip()))

    def _count_table_data_rows(table_lines: list[str]) -> int:
        """Count non-header, non-separator data rows in a table block."""
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
        """RFC-036 D0 — fraction of data rows that are single-value cells
        (axis labels), e.g. ``| 42 |`` rather than a real ``| key | value |``
        row. High ratio ⇒ chart content, not a fragmentable table."""
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
        """Return first non-separator pipe-row cell text as heading candidate."""
        for ln in table_lines:
            if _is_pipe_row(ln) and not _is_sep_row(ln):
                cells = [c.strip() for c in ln.strip().split("|") if c.strip()]
                return " | ".join(cells[:3]) if cells else ""
        return ""

    def _split_node(node: dict) -> None:  # noqa: C901, PLR0915
        """Split a single node in-place, creating child nodes."""
        text = node.get("text") or ""
        if len(text) <= _RFC029_TABLE_SEGMENT_CHAR_THRESHOLD:
            return

        lines = text.splitlines(keepends=True)
        parent_title = node.get("title") or ""

        # Locate all table blocks: (start_line_idx, end_line_idx_exclusive)
        table_spans: list[tuple[int, int]] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if _is_pipe_row(line):
                # Found start of a table block — collect until pipe rows end
                start = i
                while i < len(lines) and (_is_pipe_row(lines[i]) or lines[i].strip() == ""):
                    i += 1
                # Trim trailing blank lines from span
                end = i
                while end > start and lines[end - 1].strip() == "":
                    end -= 1
                # Only consider it a qualifying table
                table_block = lines[start:end]
                table_block_stripped = [ln.rstrip("\n") for ln in table_block]
                data_rows = _count_table_data_rows(table_block_stripped)
                if (
                    data_rows >= _RFC029_TABLE_SEGMENT_MIN_ROWS
                    and _singleton_row_ratio(table_block_stripped)
                    <= _RFC036_SINGLETON_ROW_RATIO_THRESHOLD
                ):
                    table_spans.append((start, end))
            else:
                i += 1

        if not table_spans:
            return  # no qualifying table found — leave node intact

        # Build child segments from the interleaved prose + table regions.
        children: list[dict] = []
        cursor = 0
        child_idx = 0

        for t_start, t_end in table_spans:
            # Prose segment before this table
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

            # Table segment
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

        # Trailing prose after the last table
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
            # Segmentation produced nothing useful — leave node intact
            return

        # Verify content-preservation: joined child texts must round-trip
        # to the original (strip trailing whitespace per segment).
        joined = "\n".join(c["text"] for c in children)
        if joined.replace("\n", "") != text.replace("\n", ""):
            # Safety: if content doesn't round-trip, abandon the split.
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
        node["text"] = ""  # parent text migrated to children

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


def _strip_toc_heading_nodes(nodes: list[dict]) -> list[dict]:
    """RFC-034 D11: remove nodes whose text is empty or consists only of ToC
    dot-leader lines, where the title also looks like a ToC entry."""
    result = []
    for node in nodes:
        text = (node.get("text") or "").strip()
        title = (node.get("title") or "").strip()
        text_lines = [ln for ln in text.splitlines() if ln.strip()]
        # A ToC node: empty body or all lines match the dot-leader pattern —
        # stripped only when the title also looks like a ToC entry (or is empty).
        if (not text_lines or all(_TOC_DOT_LEADER_RE.search(ln) for ln in text_lines)) and (
            _TOC_DOT_LEADER_RE.search(title) or not title
        ):
            continue
        if "nodes" in node:
            node["nodes"] = _strip_toc_heading_nodes(node["nodes"])
        result.append(node)
    return result


def _strip_toc_heading_nodes_guarded(nodes: list[dict], doc_name: str = "") -> list[dict]:
    """RFC-034 D16: guard D11's `_strip_toc_heading_nodes` against
    over-stripping long legal statutes. All-or-nothing per document: if the
    strip would reduce max depth by more than 1, or remove more than 20% of
    nodes, discard the stripped result and keep the original tree."""
    depth_before = _tree_depth(nodes)
    count_before = _tree_node_count(nodes)
    candidate = _strip_toc_heading_nodes(copy.deepcopy(nodes))
    depth_after = _tree_depth(candidate)
    count_after = _tree_node_count(candidate)
    if (depth_before - depth_after > 1) or (
        count_before > 0 and (count_before - count_after) / count_before > 0.20
    ):
        logger.warning(
            "toc_strip_skipped: %s depth %d->%d, nodes %d->%d — over-strip guard fired",
            doc_name, depth_before, depth_after, count_before, count_after,
        )
        TOC_STRIP_SKIPPED.inc()
        return nodes
    return candidate


_GARBLE_SHORT_TEXT_DEFAULT = os.getenv("GARBLE_SHORT_TEXT_DEFAULT", "true").lower() == "true"

# RFC-029 D7 (Task 5.3) — table-aware node segmentation constants.
# Node char threshold above which table-segmentation is attempted.
_RFC029_TABLE_SEGMENT_CHAR_THRESHOLD: int = int(
    os.environ.get("RFC029_TABLE_SEGMENT_CHAR_THRESHOLD", "2000")
)
# Minimum pipe-table data rows (excluding header + separator) required to
# trigger segmentation — avoids fragmenting small 2-3 row reference tables.
_RFC029_TABLE_SEGMENT_MIN_ROWS: int = int(os.environ.get("RFC029_TABLE_SEGMENT_MIN_ROWS", "5"))

# RFC-036 D0 — singleton-ratio fragmentation guard. When more than this
# fraction of a table's data rows are single-value cells (chart axis
# labels), the table is chart-content, not a real multi-column table —
# segmenting it explodes into dozens of singleton kv nodes. Skip segmentation
# and keep the block intact instead.
_RFC036_SINGLETON_ROW_RATIO_THRESHOLD: float = float(
    os.environ.get("RFC036_SINGLETON_ROW_RATIO_THRESHOLD", "0.6")
)


def _flat_text_is_garbled(
    md: str,
    expected_script: str | None = None,
    original_reason: str | None = None,
) -> bool:
    """Garble gate for flat-path markdown (mirrors _tree_is_garbled heuristics)."""
    text = md or ""
    # RFC-025 D2: garble-by-default for short post-retry text when the
    # original tree-build failure was itself a garbling reason -- avoids
    # falling through the minimum-size heuristic gates below the floor.
    if (
        _GARBLE_SHORT_TEXT_DEFAULT
        and len(text) < 200
        and original_reason in ("garbling", "node_garbling")
    ):
        return True
    # Additive OR (RFC-015 D8): sparse mixed-script mojibake, same as the tree gate.
    return _is_garbled_blob(text, expected_script=expected_script) or _has_sparse_mojibake(text)


def _looks_like_toc_page(block_text: str) -> bool:
    """Return True if text looks like a table-of-contents page (dot-leader lines)."""
    text_lines = block_text.splitlines()
    if len(text_lines) < 3:
        return False
    matches = sum(1 for ln in text_lines if _TOC_DOT_LEADER_RE.search(ln))
    return (matches / len(text_lines)) > 0.40


# Complexity grandfathered (flat-doc router, FLAT-01); see pyproject [tool.ruff].
def route_and_extract_flat(md: str) -> tuple[str, list[dict]]:  # noqa: C901, PLR0915
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

        # RFC-030 D0 — Fence-delimiter-only stripping: drop the triple-backtick
        # delimiter line itself (opening or closing, optionally with a language
        # tag) but let the enclosed content fall through to the normal
        # prose/table parsers below. No line is skipped as "content" solely for
        # being between fence markers -- a stray/unclosed fence can no longer
        # cause silent content loss.
        if stripped.startswith("```"):
            i += 1
            continue

        if stripped == "":
            flush_prose()
            i += 1
            continue

        # Design Property 5 — HR-separator stripping: skip horizontal-rule lines
        # (--- / === / ***) that serve only as visual dividers in the source.
        # RFC-030 D0 tightening: a genuine HR sits at a block boundary (start
        # of document, or immediately after a blank line / another block) —
        # `prose_buf` is empty in that case. A repeated-char line that follows
        # non-blank prose without a blank line between (prose_buf non-empty)
        # is a mid-paragraph continuation, not a divider, and must fall
        # through to normal prose handling instead of being dropped.
        if (
            not prose_buf
            and stripped
            and all(c == stripped[0] for c in stripped)
            and stripped[0] in "-=*"
            and len(stripped) >= 3
        ):
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

        # Unresolved raw <!-- image --> marker (RFC-023 D1) -> content-less
        # image block; no matching PictureResult, so no "index" is set.
        if _FLAT_RAW_IMAGE_RE.match(stripped):
            flush_prose()
            blocks.append({"role": "image"})
            i += 1
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


def _flat_block_primary_text(block: dict) -> str:
    """D0 (RFC-027): a single flat block's primary document text, excluding
    OCR/description enrichment metadata. Unlike `_flat_block_text`, image
    blocks contribute nothing here — `ocr_text`/`description` are enrichment,
    not extracted document content, and inflate char counts used for verdict
    classification (see `classify_verdict`'s `image_enrichment_promoted`
    branch). Pure."""
    text = block.get("text", "")
    if text:
        return text
    role = block.get("role")
    if role == "table":
        return "\n".join(block.get("row_records", []) or [])
    return text


def _flat_block_text(block: dict) -> str:
    """B3 (RFC-022): a single flat block's scoreable text, table-aware.

    `role="table"` blocks carry no `"text"` key by design (FLAT-05-C1) —
    parsed cell content lives in `row_records` instead. Callers that measure
    content via `block.get("text", "")` alone see 0 chars for every table
    block. Falls back to verbalized `row_records` for tables and
    `ocr_text`/`description` for images (which also carry no `"text"` key),
    mirroring `_flat_search_text`'s per-block handling. Pure."""
    text = block.get("text", "")
    if text:
        return text
    role = block.get("role")
    if role == "table":
        return "\n".join(block.get("row_records", []) or [])
    if role == "image":
        parts = [block.get("ocr_text") or "", block.get("description") or ""]
        return "\n".join(p for p in parts if p)
    return text


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
        # Finding 13 (audit 2026-07-21): flat docs are saved with a
        # doc_description (client.py:784, _generate_flat_doc_description);
        # surface it here under the same key tree docs use (client.py:848/
        # 910, helpers.py:336) so callers of flat_doc_view get a consistent
        # field across both doc shapes instead of silently dropping it.
        "doc_description": data.get("doc_description", ""),
    }
