"""Slim search view with table nodes under a token budget (RFC-052 R8 AC1/AC3, P15).

``build_search_view`` replaces ``helpers.rag._strip_text`` inside
``_search_one_doc``. Storage is never touched: every drop happens on the
rendered copy only.

Table nodes in a stored tree (inserted by ``tables/anchor.py`` after the
gate) come in two shapes:

* a **new entry** ``<node>_t<k>``: ``{node_id, type: "table", title,
  description, table_id, start_index, end_index, text: markdown}``;
* an **enriched** existing ``_seg`` node: today's ``_seg`` node plus
  ``type: "table"``, ``table_id`` and ``description``.

``tables_on=False`` renders the tree as if no table had been inserted: new
entries are omitted and enriched nodes lose ``type``/``table_id``/
``description`` (and an optional ``coverage``). On a tree without table nodes this is byte-for-byte
``_strip_text``. (``start_index``/``end_index`` written onto an enriched
``_seg`` by the anchor are kept -- the original values are not recoverable,
and they are page numbers, not table content.)
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import math
import os
import re
from collections.abc import Mapping
from typing import Any

from ..obs import decision

logger = logging.getLogger(__name__)

__all__ = [
    "TABLE_PROMPT_LINE",
    "build_search_view",
    "count_tokens",
    "token_counter_name",
]

#: The one prompt line R8 adds, only when a table entry is actually shown.
TABLE_PROMPT_LINE = (
    "Nodes with type=table are tables; select them for questions answered by figures in a table."
)

_NEW_TABLE_ID_RE = re.compile(r"_t\d+$")
#: Keys the anchor adds to an enriched ``_seg`` node (never present today).
#: ``coverage`` is optional (budget ordering only) and never rendered.
_TABLE_KEYS = frozenset({"type", "table_id", "description", "coverage"})

_ENCODING: Any = None
_ENCODING_TRIED = False


def _encoding() -> Any:
    """tiktoken ``o200k_base``, loaded lazily once; ``None`` when unavailable.

    On a no-egress pod, ``tiktoken.get_encoding`` would otherwise try an
    unbounded, synchronous ``requests.get`` for the BPE file the first time
    this runs inside the async search path, stalling the event loop for
    minutes. litellm already ships that same file (keyed by
    ``sha1(blob_url)`` per ``tiktoken.load.read_file_cached``) under
    ``litellm_core_utils/tokenizers/``, so point tiktoken's cache there
    before ever calling ``get_encoding`` — no network required.
    """
    global _ENCODING, _ENCODING_TRIED
    if not _ENCODING_TRIED:
        _ENCODING_TRIED = True
        if "TIKTOKEN_CACHE_DIR" not in os.environ:
            try:
                # Locate litellm's package dir without importing it (no LLM
                # import outside the provider layer; import is also slow).
                spec = importlib.util.find_spec("litellm")
                if spec is None or spec.origin is None:
                    raise FileNotFoundError("litellm")
                bundled_dir = os.path.join(
                    os.path.dirname(spec.origin), "litellm_core_utils", "tokenizers"
                )
                cache_key = hashlib.sha1(
                    b"https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
                ).hexdigest()
                if os.path.isfile(os.path.join(bundled_dir, cache_key)):
                    os.environ["TIKTOKEN_CACHE_DIR"] = bundled_dir
            except Exception:  # pragma: no cover - defensive; fall through to tiktoken's own path
                pass
        try:
            import tiktoken

            _ENCODING = tiktoken.get_encoding("o200k_base")
        except Exception as exc:  # ImportError, or no cached/downloadable BPE file
            logger.warning(
                "tiktoken o200k_base unavailable (%s); search budget counts chars/4",
                type(exc).__name__,
            )
            _ENCODING = None
    return _ENCODING


def count_tokens(text: str) -> int:
    """Tokens in ``text``: tiktoken ``o200k_base``, else ``ceil(chars / 4)``."""
    enc = _encoding()
    if enc is not None:
        try:
            return len(enc.encode(text, disallowed_special=()))
        except Exception:  # pragma: no cover - defensive, never break search
            pass
    return math.ceil(len(text) / 4)


def token_counter_name() -> str:
    return "tiktoken_o200k_base" if _encoding() is not None else "chars4"


def _is_table(node: Mapping) -> bool:
    return node.get("type") == "table" or "table_id" in node


def _is_new_entry(node: Mapping) -> bool:
    return _is_table(node) and bool(_NEW_TABLE_ID_RE.search(str(node.get("node_id", ""))))


def _entry(node: Mapping) -> dict:
    return {
        "node_id": node.get("node_id"),
        "type": "table",
        "title": node.get("title") or "",
        "description": node.get("description") or "",
    }


def _walk(nodes: list, tables_on: bool, dropped: set[int]) -> list:
    out: list = []
    for n in nodes:
        if _is_new_entry(n):
            if tables_on and id(n) not in dropped:
                out.append(_entry(n))
            continue
        enriched = _is_table(n)
        copy = {k: v for k, v in n.items() if k != "text" and not (enriched and k in _TABLE_KEYS)}
        if copy.get("nodes"):
            kids = _walk(copy["nodes"], tables_on, dropped)
            if kids:
                copy["nodes"] = kids
            else:
                # Every child was a new table entry: render the leaf it was
                # before the anchor gave it children.
                del copy["nodes"]
        if enriched and tables_on and id(n) not in dropped:
            copy["type"] = "table"
            copy["description"] = n.get("description") or ""
        out.append(copy)
    return out


def _collect(nodes: list, new: list, enriched: list) -> None:
    for n in nodes:
        if _is_new_entry(n):
            new.append(n)
            continue
        if _is_table(n):
            enriched.append(n)
        if n.get("nodes"):
            _collect(n["nodes"], new, enriched)


def _coverage(node: Mapping, coverage_by_table_id: Mapping[str, float] | None) -> float:
    raw = node.get("coverage")
    if raw is None and coverage_by_table_id is not None:
        raw = coverage_by_table_id.get(str(node.get("table_id")))
    try:
        return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _dumps(view: list) -> str:
    # Must match the serialisation _search_one_doc puts in the prompt.
    return json.dumps(view, indent=2)


def _estimated_cost(node: Mapping, new_entry: bool) -> int:
    if new_entry:
        return count_tokens(json.dumps(_entry(node), indent=2)) + 1
    added = '"type": "table",\n"description": ' + json.dumps(node.get("description") or "")
    return count_tokens(added) + 1


def build_search_view(
    structure: list,
    *,
    budget_tokens: int,
    tables_on: bool,
    coverage_by_table_id: Mapping[str, float] | None = None,
    doc_id: str | None = None,
) -> tuple[list, dict]:
    """Render the slim search tree; return ``(view, stats)``.

    Budget (R8 AC3, P15): ``tokens(view) - tokens(view with tables_on=False)
    <= budget_tokens``. Over budget, drop 1) new ``_t<k>`` entries, lowest
    coverage first, then 2) the added ``type``/``description`` of enriched
    ``_seg`` nodes, lowest coverage first. Coverage comes from the node's own
    ``coverage`` key, else ``coverage_by_table_id[table_id]``, else 0.

    ``stats`` keys: ``tables_on``, ``table_entry_count`` (new entries in the
    tree), ``enriched_count``, ``shown_table_count``, ``dropped_table_count``,
    ``dropped_enriched_count``, ``added_token_count``, ``budget_token_count``,
    ``counter``. Never raises on a malformed tree node; the caller falls back.

    Note: the ~20-token ``TABLE_PROMPT_LINE`` preamble is not counted against
    ``budget_tokens`` here -- R8 AC3 bounds the table *node* tokens added to
    the tree, not that fixed prompt line.
    """
    structure = structure or []
    off = _walk(structure, False, set())
    stats: dict[str, Any] = {
        "tables_on": bool(tables_on),
        "table_entry_count": 0,
        "enriched_count": 0,
        "shown_table_count": 0,
        "dropped_table_count": 0,
        "dropped_enriched_count": 0,
        "added_token_count": 0,
        "budget_token_count": int(budget_tokens),
        "counter": None,
    }
    if not tables_on:
        return off, stats

    new: list = []
    enriched: list = []
    _collect(structure, new, enriched)
    stats["table_entry_count"] = len(new)
    stats["enriched_count"] = len(enriched)
    if not new and not enriched:
        return off, stats

    stats["counter"] = token_counter_name()
    base = count_tokens(_dumps(off))
    dropped: set[int] = set()
    view = _walk(structure, True, dropped)
    added = count_tokens(_dumps(view)) - base

    if added > budget_tokens:
        order = [(n, True) for n in sorted(new, key=lambda n: _coverage(n, coverage_by_table_id))]
        order += [
            (n, False) for n in sorted(enriched, key=lambda n: _coverage(n, coverage_by_table_id))
        ]
        i = 0
        while added > budget_tokens and i < len(order):
            excess = added - budget_tokens
            saved = 0
            # Drop by estimate until the excess is covered, then recount
            # exactly: a handful of full counts instead of one per table.
            while i < len(order) and saved < excess:
                node, is_new = order[i]
                dropped.add(id(node))
                saved += _estimated_cost(node, is_new)
                i += 1
            view = _walk(structure, True, dropped)
            added = count_tokens(_dumps(view)) - base
        stats["dropped_table_count"] = sum(1 for n in new if id(n) in dropped)
        stats["dropped_enriched_count"] = sum(1 for n in enriched if id(n) in dropped)

    stats["added_token_count"] = max(0, added)
    stats["shown_table_count"] = (
        len(new) + len(enriched) - stats["dropped_table_count"] - stats["dropped_enriched_count"]
    )
    if stats["dropped_table_count"] or stats["dropped_enriched_count"]:
        decision(
            event="tables_search_budget",
            choice="dropped",
            reason="added table tokens over TABLES_SEARCH_TOKEN_BUDGET",
            attrs={
                "doc_id": doc_id,
                "table_entry_count": stats["table_entry_count"],
                "enriched_count": stats["enriched_count"],
                "dropped_table_count": stats["dropped_table_count"],
                "dropped_enriched_count": stats["dropped_enriched_count"],
                "added_token_count": stats["added_token_count"],
                "budget_token_count": stats["budget_token_count"],
                "counter": stats["counter"],
            },
            logger=logger,
        )
    return view, stats
