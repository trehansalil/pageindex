"""RFC-046 D12 (core tranche): phase and decision-layer logging.

Stdlib ``logging`` + ``extra=`` + a JSON ``Formatter`` + a ``contextvars``-
backed ``Filter``. No structlog, no new dependency.

Public surface:
  - ``JsonFormatter`` / ``ContextFilter`` -- one JSON line per record on
    stderr, correlated via contextvars (tasks 12.1, 12.2).
  - ``configure()`` -- installs the handler on the root logger (task 12.1).
  - ``bind_log_context()`` -- binds correlation fields across the process
    boundary and across recovery passes (task 12.2).
  - ``Phase`` / ``phase()`` -- the phase model (task 12.4).
  - ``decision()`` + ``DECISION_POINTS`` -- the decision layer (task 12.5).
  - ``propagate()`` -- re-binds correlation inside a ThreadPoolExecutor
    worker, which does not inherit contextvars (task 12.5).
"""
from __future__ import annotations

from .context import bind_log_context, propagate
from .decision_points import (
    DECISION_EVENTS,
    DECISION_POINTS,
    DECISION_POINTS_BY_EVENT,
    FORBIDDEN_ATTR_SUBSTRINGS,
    INSTRUMENTED_MODULES,
    DecisionPoint,
    allowed_attrs,
    allowed_choices,
    content_attr_violations,
    is_content_attr,
    point_for,
)
from .decisions import decision
from .filter import ContextFilter
from .formatter import JsonFormatter
from .log_config import configure
from .phases import Phase, phase

__all__ = [
    "DECISION_EVENTS",
    "DECISION_POINTS",
    "DECISION_POINTS_BY_EVENT",
    "FORBIDDEN_ATTR_SUBSTRINGS",
    "INSTRUMENTED_MODULES",
    "ContextFilter",
    "DecisionPoint",
    "JsonFormatter",
    "Phase",
    "allowed_attrs",
    "allowed_choices",
    "bind_log_context",
    "configure",
    "content_attr_violations",
    "decision",
    "is_content_attr",
    "phase",
    "point_for",
    "propagate",
]
