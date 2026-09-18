"""Defensive ``attrs`` sanitisation shared by the formatter, decision() and
phase() (RFC-046 D12, Property 12 / never-raise posture).

Never raises. A value that cannot be rendered degrades to a string, and a
value whose own ``__str__``/``__repr__`` also raise degrades to a fixed
placeholder -- the posture ``tracing.py:168`` already takes ("tracing must
never break the tool").
"""
from __future__ import annotations

import json
from typing import Any

from .constants import PLACEHOLDER_UNSERIALISABLE

_SCALAR_TYPES = (str, int, float, bool)


def safe_attrs(attrs: dict | None) -> dict[str, Any]:
    """Render ``attrs`` one level deep, guaranteed JSON-serialisable.

    Non-dict input (a caller passing the wrong type) is treated as empty
    rather than raising -- a malformed debug argument must never abort the
    document.
    """
    if not attrs or not isinstance(attrs, dict):
        return {}
    return {str(key): safe_scalar(value) for key, value in attrs.items()}


def safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, _SCALAR_TYPES):
        return value
    if isinstance(value, (list, tuple)):
        return [safe_scalar(item) for item in value]
    return _safe_fallback(value)


def _safe_fallback(value: Any) -> Any:
    """Last resort for anything not already a JSON scalar/array: try a
    round-trippable JSON encoding, then ``str()``, then give up safely."""
    try:
        json.dumps(value)
        return value
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return PLACEHOLDER_UNSERIALISABLE
