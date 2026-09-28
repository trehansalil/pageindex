"""Table descriptions for the search view (RFC-052 R8 AC1-2, P4-3, P16).

``describe`` returns ``{table_id: description}`` (<= ~30 tokens each) for
every record passed in. ``describe_with_sources`` returns the same keys with
``(description, description_source)``, where the source is one of
``schema.DESCRIPTION_SOURCES`` (``"llm"`` / ``"fallback"``).

* Only PyMuPDF records (``source == "pymupdf_find_tables"``) are sent to the
  LLM. A TableFormer record linked to a PyMuPDF partner copies the partner's
  description (highest-overlap partner first); an unlinked one gets the
  deterministic fallback.
* ``TABLES_DESC_MAX_PER_DOC`` LLM descriptions at most, spent on the
  highest-coverage tables first. The rest get the fallback.
* Batches of ``TABLES_DESC_BATCH`` tables per call, ``TABLES_DESC_CONCURRENCY``
  calls in flight. Per table the prompt carries title, caption, header and the
  first 3 rows, <= 400 chars.
* HR3: every call goes through ``client.llm._llm_with_retry`` so
  ``require_zdr_compliance`` gates it (primary and fallback endpoint). A
  blocked or failed call yields the fallback for its whole batch. Logs carry
  counts and exception types only -- never table text. PII corpora must also
  run with ``LANGFUSE_TRACE_CONTENT=false``.
* Never raises.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence

from .schema import SOURCE_PYMUPDF, SOURCE_TABLEFORMER, TableRecord
from .search_view import count_tokens
from .settings import DescribeSettings, describe_settings

logger = logging.getLogger(__name__)

__all__ = ["describe", "describe_with_sources", "fallback_description"]

MAX_DESC_TOKENS = 30
MAX_INPUT_CHARS = 400
SAMPLE_ROWS = 3

_PROMPT_HEAD = (
    "You describe tables extracted from a document so a search step can pick "
    "the right one.\n"
    "For each table below, write one short phrase (at most 30 tokens) naming "
    "what its figures measure and over which dimensions (e.g. years, "
    "regions). Do not repeat the numbers.\n"
    "Reply ONLY with a JSON object mapping each table_id to its description: "
    '{"<table_id>": "<description>", ...}\n\n'
    "Tables:\n"
)


def _clip_tokens(text: str, limit: int = MAX_DESC_TOKENS) -> str:
    text = " ".join(str(text).split())
    if count_tokens(text) <= limit:
        return text
    words = text.split(" ")
    while words and count_tokens(" ".join(words)) > limit:
        words.pop()
    return " ".join(words)


def fallback_description(record: TableRecord) -> str:
    """Deterministic header-row description (``description_source="fallback"``)."""
    header = [h.strip() for h in record.header if h and h.strip()]
    if header:
        text = "Table with columns: " + " | ".join(header)
    elif record.title:
        text = record.title
    else:
        text = f"Table p.{record.page_label}"
    return _clip_tokens(text)


def _sample_rows(record: TableRecord) -> list[tuple[str, ...]]:
    rows = list(record.cells)
    if rows and record.header and tuple(rows[0]) == tuple(record.header):
        rows = rows[1:]
    return rows[:SAMPLE_ROWS]


def _table_input(record: TableRecord) -> str:
    parts = []
    if record.title:
        parts.append(f"title: {record.title}")
    if record.caption and record.caption != record.title:
        parts.append(f"caption: {record.caption}")
    if record.header:
        parts.append("header: " + " | ".join(record.header))
    rows = _sample_rows(record)
    if rows:
        parts.append("rows: " + " / ".join(" | ".join(r) for r in rows))
    prefix = f"[{record.table_id}] "
    body = " ; ".join(" ".join(p.split()) for p in parts)
    return prefix + body[: max(0, MAX_INPUT_CHARS - len(prefix))]


def _parse(raw: str) -> dict:
    text = (raw or "").strip()
    fence = re.search(r"\{.*\}", text, re.DOTALL)
    if fence:
        text = fence.group(0)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _complete(prompt: str, model: str) -> str:
    """One description call through ``_llm_with_retry`` (HR3 gate)."""
    from ..client.llm import _llm_with_retry, get_openai_client

    resolved = model[len("azure/") :] if model.startswith("azure/") else model

    async def call_fn(base_url: str | None = None) -> str:
        client = get_openai_client()
        if base_url:
            client = client.with_options(base_url=base_url)
        r = await client.chat.completions.create(
            model=resolved,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return (r.choices[0].message.content or "").strip()

    return await _llm_with_retry(call_fn)


async def _describe_batch(
    batch: Sequence[TableRecord], model: str, sem: asyncio.Semaphore
) -> dict[str, str]:
    prompt = _PROMPT_HEAD + "\n".join(_table_input(t) for t in batch)
    async with sem:
        try:
            raw = await _complete(prompt, model)
        except Exception as exc:  # ZDRComplianceError, LLMTransientFailure, anything
            logger.warning(
                "table descriptions: batch of %d fell back (%s)", len(batch), type(exc).__name__
            )
            return {}
    parsed = _parse(raw)
    out: dict[str, str] = {}
    for t in batch:
        value = parsed.get(t.table_id)
        if isinstance(value, str) and value.strip():
            out[t.table_id] = _clip_tokens(value)
    return out


async def describe_with_sources(
    tables: list[TableRecord],
    *,
    model: str,
    cfg: DescribeSettings | None = None,
) -> dict[str, tuple[str, str]]:
    """``{table_id: (description, description_source)}`` for every record.

    ``cfg`` defaults to ``tables.settings.describe_settings()``; ``model``
    always wins over ``cfg.model``.
    """
    try:
        return await _describe_with_sources(tables, model=model, cfg=cfg or describe_settings())
    except Exception as exc:  # pragma: no cover - last-resort never-raise guard
        logger.warning("table descriptions: all fell back (%s)", type(exc).__name__)
        out: dict[str, tuple[str, str]] = {}
        for t in tables:
            try:
                out[t.table_id] = (fallback_description(t), "fallback")
            except Exception:
                out[t.table_id] = (f"Table p.{getattr(t, 'page_label', '')}", "fallback")
        return out


async def _describe_with_sources(
    tables: list[TableRecord],
    *,
    model: str,
    cfg: DescribeSettings,
) -> dict[str, tuple[str, str]]:
    if not tables:
        return {}
    batch_size = max(1, int(cfg.batch))
    concurrency = max(1, int(cfg.concurrency))
    max_per_doc = max(0, int(cfg.max_per_doc))
    enabled = cfg.enabled

    pymupdf = [t for t in tables if t.source == SOURCE_PYMUPDF]
    # Cap spent on the highest-coverage tables first (stable on ties).
    chosen = sorted(pymupdf, key=lambda t: -t.coverage)[:max_per_doc] if enabled and model else []

    llm: dict[str, str] = {}
    if chosen:
        sem = asyncio.Semaphore(concurrency)
        batches = [chosen[i : i + batch_size] for i in range(0, len(chosen), batch_size)]
        for part in await asyncio.gather(*(_describe_batch(b, model, sem) for b in batches)):
            llm.update(part)

    result: dict[str, tuple[str, str]] = {}
    for t in pymupdf:
        if t.table_id in llm:
            result[t.table_id] = (llm[t.table_id], "llm")
        else:
            result[t.table_id] = (fallback_description(t), "fallback")

    for t in tables:
        if t.source == SOURCE_PYMUPDF:
            continue
        partner = None
        if t.source == SOURCE_TABLEFORMER:
            for link in sorted(t.links, key=lambda lk: -lk.overlap):
                if link.table_id in result:
                    partner = result[link.table_id]
                    break
        result[t.table_id] = partner or (fallback_description(t), "fallback")

    logger.info(
        "table descriptions: %d tables, %d sent to LLM, %d llm, %d fallback",
        len(tables),
        len(chosen),
        sum(1 for _, src in result.values() if src == "llm"),
        sum(1 for _, src in result.values() if src == "fallback"),
    )
    return result


async def describe(tables: list[TableRecord], *, model: str) -> dict[str, str]:
    """``{table_id: description}`` (<= ~30 tokens) for every record; never raises."""
    return {
        tid: desc
        for tid, (desc, _src) in (await describe_with_sources(tables, model=model)).items()
    }
