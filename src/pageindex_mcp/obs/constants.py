"""Named constants for the ``obs`` package (RFC-046 D12, core tranche).

No magic numbers/strings in the rest of the package -- everything that names
a schema field, a record ``kind``, or a cross-process env var lives here.
"""

from __future__ import annotations

#: Frozen envelope schema version (RFC-046 R12.1). Never changes silently --
#: a schema break bumps this and is a deliberate, reviewed decision.
SCHEMA_VERSION = 1

#: Correlation fields carried by every record, bound via contextvars and
#: attached by ``ContextFilter`` on the root handler (R12.3).
CORRELATION_FIELDS: tuple[str, ...] = (
    "run_id",
    "job_id",
    "doc_sha8",
    "doc_id",
    "doc_name",
    "phase",
    "phase_seq",
)

#: Decision-record fields (R12.5). Populated by ``decision()``; absent (None)
#: on a plain ``kind="log"`` record from one of the 48 unmodified modules.
DECISION_FIELDS: tuple[str, ...] = ("event", "choice", "reason")

#: ``kind`` values. "log" is the auto-wrap default for every existing,
#: unmodified ``logging.getLogger(...)`` call site -- the whole point of
#: attaching the formatter/filter to the root handler instead of editing
#: 48 call sites.
KIND_LOG = "log"
KIND_DECISION = "decision"
KIND_PHASE_ENTRY = "phase_entry"
KIND_PHASE_EXIT = "phase_exit"

#: Placeholder substituted for an ``attrs`` value that cannot be rendered at
#: all (its ``__repr__``/``__str__`` themselves raise). decision()/phase()
#: must never break the document over a hostile object in a debug attrs dict.
PLACEHOLDER_UNSERIALISABLE = "<unserialisable>"

#: The env var carrying the correlation mapping across the converters_cli
#: child-process boundary, following the ``PAGEINDEX_JOB_START_CONFIG``
#: precedent at ``worker/subprocess_mgr.py:125`` (R12.3): env var, not
#: argv/stdin, because converters_cli reserves stdout for exactly two JSON
#: lines.
ENV_LOG_CONTEXT = "PAGEINDEX_LOG_CONTEXT"

#: Env var read once at import by ``log_config.py`` (task 12.7's stub here --
#: only the level is read in this core tranche). Deliberately NOT registered
#: in ``PipelineConfig.from_env``: see ``log_config.py`` docstring.
ENV_LOG_LEVEL = "PAGEINDEX_LOG_LEVEL"

#: Default level when ``PAGEINDEX_LOG_LEVEL`` is unset or unrecognised.
DEFAULT_LOG_LEVEL_NAME = "INFO"

#: Marker attribute set on handlers this package installs, so ``configure()``
#: can replace its own prior handler instead of duplicating it.
HANDLER_MARKER = "_pageindex_obs_handler"

#: Logger name used by decision()/phase() when the caller supplies none.
DEFAULT_LOGGER_NAME = "pageindex_mcp.obs"
