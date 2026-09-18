"""``configure()`` -- installs the JSON stderr handler on the root logger
(RFC-046 D12, task 12.1; stub of task 12.7's env surface for this core
tranche -- only the level is read here).

Reads ``PAGEINDEX_LOG_LEVEL`` once, at import, in this module alone.
Deliberately NOT added to ``PipelineConfig.from_env``:
``TestNoConfigDoubleSourcing`` (``test_architecture_guards.py:~1430``) derives
its "owned" env-var set from ``from_env`` and scans all of ``src/``
closed-world, so registering ``PAGEINDEX_LOG_*`` there would make this
module's own read a violation of the very guard meant to prevent double
sourcing.
"""

from __future__ import annotations

import logging
import os
import sys

from .constants import DEFAULT_LOG_LEVEL_NAME, ENV_LOG_LEVEL, HANDLER_MARKER
from .filter import ContextFilter
from .formatter import JsonFormatter

_LOG_LEVEL_NAME = os.environ.get(ENV_LOG_LEVEL, DEFAULT_LOG_LEVEL_NAME).upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_NAME, logging.INFO)


def configure(level: int | None = None) -> None:
    """Install (or replace) the JSON stderr handler on the root logger.

    The stream is always ``sys.stderr`` -- ``converters_cli`` reserves stdout
    for exactly two JSON lines, so a handler defaulting to stdout would fail
    every job with "invalid JSON on stdout" (Property 13). Idempotent: any
    handler this module previously installed (tagged via ``HANDLER_MARKER``)
    is removed first, so repeated calls never duplicate output.

    Also removes any untagged handler (e.g. one installed by a prior
    ``logging.basicConfig`` call) so that callers migrating from basicConfig
    to ``configure()`` get exactly one handler, not two (task 12.9).
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, HANDLER_MARKER, True)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ContextFilter())
    root.addHandler(handler)
    root.setLevel(level if level is not None else _LOG_LEVEL)
