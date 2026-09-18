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
    # RFC-046 task 12.2: the Langfuse trace id for the enclosing tool call,
    # bound by tracing.trace_tool(). It is what joins the two observability
    # surfaces -- without it Langfuse knows the trace, the logs know the
    # document, and nothing knows both.
    "trace_id",
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

#: Env vars read once at import by ``log_config.py`` (task 12.7), and nowhere
#: else in ``src/``. Deliberately NOT registered in ``PipelineConfig.from_env``:
#: see ``log_config.py``'s docstring for why that would be self-defeating.
ENV_LOG_LEVEL = "PAGEINDEX_LOG_LEVEL"
ENV_LOG_NODE_SAMPLE = "PAGEINDEX_LOG_NODE_SAMPLE"
ENV_LOG_DECISIONS = "PAGEINDEX_LOG_DECISIONS"
ENV_LOG_CONTENT = "PAGEINDEX_LOG_CONTENT"

#: Default level when ``PAGEINDEX_LOG_LEVEL`` is unset or unrecognised.
DEFAULT_LOG_LEVEL_NAME = "INFO"

#: Truncation bound applied to any length-bearing excerpt a record may carry.
#: ``PAGEINDEX_LOG_CONTENT=true`` swaps the first for the second -- it widens a
#: bound, it never unmasks full text, and the wide bound is still finite
#: (R12.7). Neither value ever permits a node title, summary or OCR output:
#: those are refused by key, not truncated (see ``is_content_attr``).
CONTENT_TRUNCATION_CHARS = 120
CONTENT_TRUNCATION_CHARS_WIDE = 512

#: Marker attribute set on handlers this package installs, so ``configure()``
#: can replace its own prior handler instead of duplicating it.
HANDLER_MARKER = "_pageindex_obs_handler"

#: Logger name used by decision()/phase() when the caller supplies none.
DEFAULT_LOGGER_NAME = "pageindex_mcp.obs"
