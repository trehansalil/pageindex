"""worker package — split from monolith worker.py for maintainability.

Backward-compatible re-exports: ``from pageindex_mcp.worker import X`` still works.
"""

from .constants import (
    CHILD_GRACE_SECONDS,
    CHILD_TIMEOUT,
    JOB_TIMEOUT,
    REAP_GRACE,
)
from .errors import (
    _CHILD_ERROR_REGISTRY,
    _DEFAULT_CHILD_CLASSIFICATION,
    _LLM_TERMINAL_INDICATORS,
    _TERMINAL_CHILD_REASONS,
    ChildErrorClassification,
    _classify_llm_failure,
)
from .job import (
    DLQ_KEY,
    JOB_TTL,
    MAX_TRIES,
    _dlq_push_on_final_attempt,
    process_document_job,
    reap_stale_jobs,
)
from .lifecycle import (
    MAX_JOBS,
    MAX_JOBS_CEILING,
    MAX_JOBS_DEFAULT,
    WorkerSettings,
    _reconcile_registry_drift_cron,
    resolve_max_jobs,
    shutdown,
    startup,
)
from .registry_mirror import (
    _VERDICT_RETRY_KEY_PREFIX,
    _VERDICT_RETRY_TTL_S,
    _enqueue_verdict_retry,
    _mirror_bridged_incr,
    _mirror_bridged_set,
    _mirror_registry_metric_to_redis,
    _mirror_registry_write_failure_to_redis,
    _upsert_registry_row,
)
from .subprocess_mgr import (
    KILL_GRACE_SECONDS,
    ConverterChildError,
    ConverterOOMError,
    _kill_group,
    _run_converter_subprocess,
)

__all__ = [
    "CHILD_GRACE_SECONDS",
    "CHILD_TIMEOUT",
    "DLQ_KEY",
    "JOB_TIMEOUT",
    "JOB_TTL",
    "KILL_GRACE_SECONDS",
    "MAX_JOBS",
    "MAX_JOBS_CEILING",
    "MAX_JOBS_DEFAULT",
    "MAX_TRIES",
    "REAP_GRACE",
    "_CHILD_ERROR_REGISTRY",
    "_DEFAULT_CHILD_CLASSIFICATION",
    "_LLM_TERMINAL_INDICATORS",
    "_TERMINAL_CHILD_REASONS",
    "_VERDICT_RETRY_KEY_PREFIX",
    "_VERDICT_RETRY_TTL_S",
    "ChildErrorClassification",
    "ConverterChildError",
    "ConverterOOMError",
    "WorkerSettings",
    "_classify_llm_failure",
    "_dlq_push_on_final_attempt",
    "_enqueue_verdict_retry",
    "_kill_group",
    "_mirror_bridged_incr",
    "_mirror_bridged_set",
    "_mirror_registry_metric_to_redis",
    "_mirror_registry_write_failure_to_redis",
    "_reconcile_registry_drift_cron",
    "_run_converter_subprocess",
    "_upsert_registry_row",
    "process_document_job",
    "reap_stale_jobs",
    "resolve_max_jobs",
    "shutdown",
    "startup",
]
