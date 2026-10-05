"""Every ``TABLES_*`` knob of RFC-052 P4 (design "Table Capture" and
"Tables in Search"), read from the environment at call time.

Worker: capture (``capture_settings``) and descriptions
(``describe_settings``). Server: search view (``search_settings``).
The service-side bypass switches (``TABLES_OCR_BYPASS``, ``TABLES_TRUST_*``,
``TABLES_OCR_BYPASS_MIN_FILLED``) live in ``config.py``, not here.

A malformed value falls back to its default with a WARNING; it never raises.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

from .schema import STRATEGIES

logger = logging.getLogger(__name__)

_MIB = 1024 * 1024
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    v = raw.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    logger.warning("%s=%r is not a boolean; using %s", key, raw, default)
    return default


def _num(env: Mapping[str, str], key: str, default, cast, minimum):
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(float(raw.strip())) if cast is int else cast(raw.strip())
    except ValueError:
        logger.warning("%s=%r is not a number; using %s", key, raw, default)
        return default
    if not math.isfinite(value):
        logger.warning("%s=%r is not finite; using %s", key, raw, default)
        return default
    if value < minimum:
        logger.warning("%s=%r is below %s; using %s", key, raw, minimum, default)
        return default
    return value


def _default_pod_slots() -> int:
    from ..converters.docling_resources import available_cpus

    return max(1, int(available_cpus()))


def _strategies(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = env.get("TABLES_STRATEGIES")
    if raw is None or not raw.strip():
        return STRATEGIES
    wanted = [s.strip().lower() for s in raw.split(",") if s.strip()]
    valid = tuple(dict.fromkeys(s for s in wanted if s in STRATEGIES))
    if not valid:
        logger.warning("TABLES_STRATEGIES=%r names no known strategy; using %s", raw, STRATEGIES)
        return STRATEGIES
    return valid


@dataclass(frozen=True)
class CaptureSettings:
    enabled: bool  # TABLES_CAPTURE
    proc_bytes: int  # TABLES_PROC_BYTES
    reserve_bytes: int  # TABLES_RESERVE_BYTES
    min_pages_per_proc: int  # TABLES_MIN_PAGES_PER_PROC
    pod_slots: int  # TABLES_POD_SLOTS
    rss_poll_s: float  # TABLES_RSS_POLL_S
    join_grace_s: float | None  # TABLES_JOIN_GRACE_S; None = until the capture deadline
    deadline_margin_s: float  # TABLES_DEADLINE_MARGIN_S
    strategies: tuple[str, ...]  # TABLES_STRATEGIES
    link_min_overlap: float  # TABLES_LINK_MIN_OVERLAP


def capture_settings(env: Mapping[str, str] | None = None) -> CaptureSettings:
    e = _env(env)
    overlap = _num(e, "TABLES_LINK_MIN_OVERLAP", 0.5, float, 0.0)
    if overlap > 1.0 or overlap <= 0.0:
        logger.warning("TABLES_LINK_MIN_OVERLAP=%r outside (0, 1]; using 0.5", overlap)
        overlap = 0.5
    raw_slots = e.get("TABLES_POD_SLOTS")
    pod_slots = (
        _num(e, "TABLES_POD_SLOTS", 0, int, 1) if raw_slots is not None and raw_slots.strip() else 0
    )
    return CaptureSettings(
        enabled=_bool(e, "TABLES_CAPTURE", True),
        # Sized for the 1536 MiB worker pod (11.7 re-run): the converter child
        # and arq leave ~614 MiB, and these start two processes there. 192 MiB
        # is 1.5x the highest capture RSS seen (129 MiB, a 292-page tables-heavy
        # document); two at that cap still leave ~200 MiB of the pod.
        proc_bytes=_num(e, "TABLES_PROC_BYTES", 192 * _MIB, int, 1),
        reserve_bytes=_num(e, "TABLES_RESERVE_BYTES", 128 * _MIB, int, 0),
        min_pages_per_proc=_num(e, "TABLES_MIN_PAGES_PER_PROC", 30, int, 1),
        pod_slots=pod_slots or _default_pod_slots(),
        rss_poll_s=_num(e, "TABLES_RSS_POLL_S", 0.25, float, 0.01),
        join_grace_s=_num(e, "TABLES_JOIN_GRACE_S", None, float, 0.0),
        deadline_margin_s=_num(e, "TABLES_DEADLINE_MARGIN_S", 60.0, float, 0.0),
        strategies=_strategies(e),
        link_min_overlap=overlap,
    )


@dataclass(frozen=True)
class SearchSettings:
    in_search: bool  # TABLES_IN_SEARCH (0 = today's _strip_text view)
    token_budget: int  # TABLES_SEARCH_TOKEN_BUDGET


def search_settings(env: Mapping[str, str] | None = None) -> SearchSettings:
    e = _env(env)
    return SearchSettings(
        in_search=_bool(e, "TABLES_IN_SEARCH", True),
        token_budget=_num(e, "TABLES_SEARCH_TOKEN_BUDGET", 8000, int, 0),
    )


@dataclass(frozen=True)
class DescribeSettings:
    enabled: bool  # TABLES_DESC_ENABLED
    model: str  # TABLES_DESC_MODEL (default PAGEINDEX_FILTER_MODEL, P4-3)
    batch: int  # TABLES_DESC_BATCH
    concurrency: int  # TABLES_DESC_CONCURRENCY
    max_per_doc: int
    # TABLES_DESC_DEADLINE_S -- bound on PendingTables.finalize's wait for
    # the describe task; used when no converter-child/job deadline is
    # reachable (or as the ceiling on the remaining time when one is).
    deadline_s: float = 60.0


def describe_settings(env: Mapping[str, str] | None = None) -> DescribeSettings:
    e = _env(env)
    model = (e.get("TABLES_DESC_MODEL") or "").strip()
    if not model:
        from ..config import settings

        model = settings.llm_filter_model  # PAGEINDEX_FILTER_MODEL
    return DescribeSettings(
        enabled=_bool(e, "TABLES_DESC_ENABLED", True),
        model=model,
        batch=_num(e, "TABLES_DESC_BATCH", 25, int, 1),
        concurrency=_num(e, "TABLES_DESC_CONCURRENCY", 4, int, 1),
        max_per_doc=_num(e, "TABLES_DESC_MAX_PER_DOC", 600, int, 0),
        deadline_s=_num(e, "TABLES_DESC_DEADLINE_S", 60, float, 0.01),
    )
