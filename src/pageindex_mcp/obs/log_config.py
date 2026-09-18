"""``configure()`` plus the whole ``PAGEINDEX_LOG_*`` env surface
(RFC-046 D12, tasks 12.1 and 12.7).

Every variable is read ONCE, at import, in this module alone; the rest of the
package -- and the six hot-path files task 12.5 instruments -- import the
resolved constant. ``TestHotPathConfigAccessGuard``
(``test_architecture_guards.py:772-838``) forbids an inline ``os.environ``
read in those files, and per-record env lookups would be a hot-path cost for
a value that cannot change within a process anyway.
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

from .constants import (
    CONTENT_TRUNCATION_CHARS,
    CONTENT_TRUNCATION_CHARS_WIDE,
    DEFAULT_LOG_LEVEL_NAME,
    ENV_LOG_CONTENT,
    ENV_LOG_DECISIONS,
    ENV_LOG_LEVEL,
    ENV_LOG_NODE_SAMPLE,
    HANDLER_MARKER,
)
from .filter import ContextFilter
from .formatter import JsonFormatter

_TRUE_WORDS = frozenset({"1", "on", "true", "yes", "y"})
_FALSE_WORDS = frozenset({"0", "off", "false", "no", "n"})


def _parse_switch(raw: str | None, *, default: bool) -> bool:
    """An on/off env var. Anything unrecognised -- including the empty string
    a shell leaves behind for ``FOO=`` -- falls back to *default* rather than
    raising: a typo in a deployment variable must not stop the process from
    logging."""
    if raw is None:
        return default
    word = raw.strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    return default


def _parse_count(raw: str | None, *, default: int) -> int:
    """A non-negative integer env var. Unparseable or negative -> *default*.
    Same reasoning as ``_parse_switch``: never raise out of module import."""
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (ValueError, AttributeError):
        return default
    return value if value >= 0 else default


_LOG_LEVEL_NAME = os.environ.get(ENV_LOG_LEVEL, DEFAULT_LOG_LEVEL_NAME).upper()

#: Root level for the handler ``configure()`` installs.
LOG_LEVEL: int = getattr(logging, _LOG_LEVEL_NAME, logging.INFO)

#: How many per-node records a capped, repeating decision point may emit for
#: one document. 0 (the default) means "no per-node sampling" -- the capped
#: DEBUG points in ``garble.py`` stay silent unless this is raised.
LOG_NODE_SAMPLE: int = _parse_count(os.environ.get(ENV_LOG_NODE_SAMPLE), default=0)

#: Kill switch for the decision layer alone. Off silences every
#: ``decision()`` call; ordinary log records are unaffected. On by default --
#: R12.6 requires the decision layer visible at INFO in a normal run.
LOG_DECISIONS_ENABLED: bool = _parse_switch(os.environ.get(ENV_LOG_DECISIONS), default=True)

#: Widens the excerpt truncation bound (see ``CONTENT_TRUNCATION_CHARS``).
#: It does NOT unmask anything: content-bearing keys are refused outright by
#: ``is_content_attr`` regardless of this flag.
LOG_CONTENT_WIDENED: bool = _parse_switch(os.environ.get(ENV_LOG_CONTENT), default=False)

#: The bound actually in force for this process.
TRUNCATION_CHARS: int = (
    CONTENT_TRUNCATION_CHARS_WIDE if LOG_CONTENT_WIDENED else CONTENT_TRUNCATION_CHARS
)


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
    root.setLevel(level if level is not None else LOG_LEVEL)
