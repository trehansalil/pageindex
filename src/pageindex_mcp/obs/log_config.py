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
    DEFAULT_LOKI_SERVICE,
    ENV_LOG_CONTENT,
    ENV_LOG_DECISIONS,
    ENV_LOG_FILE,
    ENV_LOG_LEVEL,
    ENV_LOKI_PUSH_URL,
    ENV_LOKI_SERVICE,
    HANDLER_MARKER,
    LOG_FILE_BACKUPS,
    LOG_FILE_MAX_BYTES,
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


_LOG_LEVEL_NAME = os.environ.get(ENV_LOG_LEVEL, DEFAULT_LOG_LEVEL_NAME).upper()

#: Root level for the handler ``configure()`` installs.
#  ``getattr(logging, ...)`` also resolves non-level attributes: a stray
#  PAGEINDEX_LOG_LEVEL=BASIC_FORMAT returned a str, and ``root.setLevel()``
#  then raised out of ``configure()`` instead of taking the documented INFO
#  fallback. The name mapping only contains actual levels.
LOG_LEVEL: int = logging.getLevelNamesMapping().get(_LOG_LEVEL_NAME, logging.INFO)

#: Kill switch for the decision layer alone. Off silences every
#: ``decision()`` call; ordinary log records are unaffected. On by default --
#: R12.6 requires the decision layer visible at INFO in a normal run.
LOG_DECISIONS_ENABLED: bool = _parse_switch(os.environ.get(ENV_LOG_DECISIONS), default=True)

#: Widens the excerpt truncation bound (see ``CONTENT_TRUNCATION_CHARS``).
#: It does NOT unmask anything: content-bearing keys are refused outright by
#: ``is_content_attr`` regardless of this flag.
LOG_CONTENT_WIDENED: bool = _parse_switch(os.environ.get(ENV_LOG_CONTENT), default=False)

#: Optional log file replacing stderr (RFC-052 task 1.10). Only the Mac
#: docling-service sets it: launchd's StandardOutPath holds one fd open for the
#: life of the process, so it cannot be rotated from outside without sudo
#: (newsyslog). The service rotates its own file instead (``RotatingFileHandler``,
#: 10 MB x 5); spawned chunk children inherit the variable and append through a
#: ``WatchedFileHandler``, which reopens the path after the parent's rename.
#: Empty/unset -> stderr, as before.
LOG_FILE: str | None = os.environ.get(ENV_LOG_FILE, "").strip() or None

#: Optional Loki push endpoint (``obs/loki.py``). Set -> every process that
#: calls ``configure()`` -- chunk children included -- ships its own records.
LOKI_PUSH_URL: str | None = os.environ.get(ENV_LOKI_PUSH_URL, "").strip() or None
LOKI_SERVICE: str = os.environ.get(ENV_LOKI_SERVICE, "").strip() or DEFAULT_LOKI_SERVICE

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

    With ``PAGEINDEX_LOKI_PUSH_URL`` set, a second tagged handler ships the
    same JSON lines to Loki (RFC-052 task 1.10). A spawned chunk child calls
    this too (``docling_conv._docling_chunk_worker``) and so ships its own
    records; its ``atexit`` drains them before the child exits.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
        # Our own prior handlers may hold a file (PAGEINDEX_LOG_FILE) or a
        # sender thread (Loki); close them rather than leak. A StreamHandler's
        # close() leaves stderr open.
        if getattr(existing, HANDLER_MARKER, False):
            existing.close()

    handlers: list[logging.Handler] = [_local_handler()]
    if LOKI_PUSH_URL:
        from .loki import LokiPushHandler, stream_labels

        handlers.append(LokiPushHandler(LOKI_PUSH_URL, labels=stream_labels(LOKI_SERVICE)))
    for handler in handlers:
        setattr(handler, HANDLER_MARKER, True)
        handler.setFormatter(JsonFormatter())
        handler.addFilter(ContextFilter())
        root.addHandler(handler)
    root.setLevel(level if level is not None else LOG_LEVEL)


def _local_handler() -> logging.Handler:
    """stderr, or ``PAGEINDEX_LOG_FILE``: rotated in-process by the top
    process, followed across that rotation by spawned (multiprocessing)
    children, which must never rotate it themselves."""
    if not LOG_FILE:
        return logging.StreamHandler(sys.stderr)
    import multiprocessing
    from logging.handlers import RotatingFileHandler, WatchedFileHandler

    if multiprocessing.parent_process() is None:
        return RotatingFileHandler(
            LOG_FILE, maxBytes=LOG_FILE_MAX_BYTES, backupCount=LOG_FILE_BACKUPS, encoding="utf-8"
        )
    return WatchedFileHandler(LOG_FILE, encoding="utf-8")
