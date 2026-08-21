from __future__ import annotations

import dataclasses

# ---------------------------------------------------------------------------
# Zone 6 (Part A): ChildErrorClassification — exhaustive child error registry
# ---------------------------------------------------------------------------
# The converter child (converters_cli.py:176) emits ``type(exc).__name__`` as the
# ``error`` field.  This registry maps every known exception class name to a stable
# Redis ``reason`` code and a ``terminal`` flag (True = deterministic w.r.t. input;
# retrying wastes worker time).  Unknown classes fall through to
# ``_DEFAULT_CHILD_CLASSIFICATION`` so the reason field remains a finite,
# machine-consumable set.
#
# ``LLMTransientFailure`` is intentionally ABSENT — it is classified by
# ``_classify_llm_failure`` (see below) before the registry lookup fires.


@dataclasses.dataclass(frozen=True, slots=True)
class ChildErrorClassification:
    """Frozen classification of a child-reported exception class."""

    reason: str
    terminal: bool


_CHILD_ERROR_REGISTRY: dict[str, ChildErrorClassification] = {
    # Deterministic: same input always produces same failure → no retry
    "LowQualityTreeError": ChildErrorClassification("low_quality_tree", terminal=True),
    "TessdataUnavailableError": ChildErrorClassification("converter_env_missing", terminal=True),
    "FuturesTimeoutError": ChildErrorClassification("converter_timeout", terminal=True),
    # Transient: may recover on retry (MinIO glitch, transient env issue, etc.)
    "FileNotFoundError": ChildErrorClassification("input_missing", terminal=False),
    "RuntimeError": ChildErrorClassification("converter_child_failed", terminal=False),
    "ArgparseExit": ChildErrorClassification("converter_child_failed", terminal=False),
    "HeaderNotFoundException": ChildErrorClassification("converter_child_failed", terminal=False),
    "ImplausibleHeadingStructureException": ChildErrorClassification(
        "converter_child_failed",
        terminal=False,
    ),
    "TypeError": ChildErrorClassification("converter_child_failed", terminal=False),
}

# Default for unknown exception classes: transient (fail-open toward retry).
_DEFAULT_CHILD_CLASSIFICATION = ChildErrorClassification("converter_child_failed", terminal=False)

# Reasons that are deterministic with respect to the input document: retrying
# the same job on the same staged file will produce the same failure, so arq
# retries / DLQ pushes only waste worker time. We treat these as terminal —
# write the Redis status, purge staging, and swallow the exception so arq
# does not requeue. ``input_missing`` is NOT in this set: a transient MinIO
# read failure can in principle recover on retry, and the wasted retry on a
# genuinely-missing file is cheap (one extra download attempt).
#
# Derived from the registry + the LLM-failure classifier's terminal output.
_TERMINAL_CHILD_REASONS: frozenset[str] = frozenset(
    c.reason for c in _CHILD_ERROR_REGISTRY.values() if c.terminal
) | {"llm_failure_terminal"}

# Module-level exhaustiveness assertion (Part E): every terminal reason in the
# registry is in _TERMINAL_CHILD_REASONS and vice versa, accounting for
# ``llm_failure_terminal`` which comes from _classify_llm_failure (not the
# registry).
_registry_terminal_reasons = frozenset(
    c.reason for c in _CHILD_ERROR_REGISTRY.values() if c.terminal
)
_expected_terminal = _registry_terminal_reasons | {"llm_failure_terminal"}
assert _expected_terminal == _TERMINAL_CHILD_REASONS, (
    f"_TERMINAL_CHILD_REASONS is out of sync with _CHILD_ERROR_REGISTRY: "
    f"symmetric diff = {_TERMINAL_CHILD_REASONS ^ _expected_terminal}"
)
del _registry_terminal_reasons, _expected_terminal
# Substrings in an LLMTransientFailure's stderr_tail that indicate a
# deterministic failure (retrying the same input reproduces it). Checked
# before any rate-limit/transient indicator so a stderr_tail carrying both
# (e.g. a rate-limited request whose retry then hit a CMap-corrupt PDF)
# still classifies as terminal rather than looping arq retries forever.
_LLM_TERMINAL_INDICATORS = ("CMap", "content_policy", "content_filter")


def _classify_llm_failure(stderr_tail: str) -> str:
    """Classify an ``LLMTransientFailure`` child error as terminal or transient.

    Terminal (no retry): CMap corruption or content-policy/content-filter
    rejection -- deterministic with respect to the input document. Transient
    (retryable, MAX_TRIES): rate-limit/throttling indicators, and any
    unrecognized detail -- fails open toward retry rather than toward silent
    data loss.
    """
    if any(indicator in stderr_tail for indicator in _LLM_TERMINAL_INDICATORS):
        return "llm_failure_terminal"
    return "llm_failure_transient"
