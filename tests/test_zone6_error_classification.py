"""Zone 6 (Part A): ChildErrorClassification exhaustiveness tests.

Verifies that _CHILD_ERROR_REGISTRY covers every exception class name the
converter child (converters_cli.py) can emit, that _TERMINAL_CHILD_REASONS
is exactly the union of registry-terminal reasons + llm_failure_terminal,
and that specific regression-critical mappings are stable.
"""
from __future__ import annotations

import ast
import os

import pytest

from pageindex_mcp.worker import (
    ChildErrorClassification,
    _CHILD_ERROR_REGISTRY,
    _DEFAULT_CHILD_CLASSIFICATION,
    _TERMINAL_CHILD_REASONS,
    _classify_llm_failure,
)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONVERTERS_CLI = os.path.join(
    _PROJECT_ROOT, "src", "pageindex_mcp", "converters_cli.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ast_collect_raised_exception_names(filepath: str) -> set[str]:
    """Parse *filepath* and return every exception class name that appears in
    a ``raise ExcType(...)`` statement (direct Name nodes only -- attribute
    access like ``module.Exc`` is not relevant here because converters_cli
    raises plain names caught by the broad ``except Exception as exc``
    handler whose ``type(exc).__name__`` is what the worker classifies).

    Also collects hardcoded ``"error"`` strings written to JSON output
    (e.g. ``"ArgparseExit"`` emitted directly without a raise).
    """
    with open(filepath) as f:
        source = f.read()
    tree = ast.parse(source, filename=filepath)
    names: set[str] = set()
    for node in ast.walk(tree):
        # Direct raise: ``raise SomeError(...)``
        if isinstance(node, ast.Raise) and node.exc is not None:
            call = node.exc
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                names.add(call.func.id)
            elif isinstance(call, ast.Name):
                names.add(call.id)
        # Hardcoded error strings in JSON payloads: {"error": "ArgparseExit"}
        # These are ast.Constant values assigned under a dict key "error".
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "error"
                    and isinstance(v, ast.Constant)
                    and isinstance(v.value, str)
                ):
                    names.add(v.value)
    return names


def _ast_collect_except_handler_types(filepath: str) -> set[str]:
    """Return all exception type names caught by except-handlers in the file.

    These represent types the code is aware of and whose __name__ could
    appear in the ``type(exc).__name__`` output.
    """
    with open(filepath) as f:
        source = f.read()
    tree = ast.parse(source, filename=filepath)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            if isinstance(node.type, ast.Name):
                names.add(node.type.id)
            elif isinstance(node.type, ast.Tuple):
                for elt in node.type.elts:
                    if isinstance(elt, ast.Name):
                        names.add(elt.id)
    return names


# ---------------------------------------------------------------------------
# 1. Exhaustiveness
# ---------------------------------------------------------------------------

class TestChildErrorRegistryExhaustiveness:
    """Every exception class name that converters_cli can produce via
    type(exc).__name__ must be present in _CHILD_ERROR_REGISTRY (or be
    LLMTransientFailure, which is handled separately)."""

    def test_all_converters_cli_exceptions_covered(self):
        """AST-parse converters_cli.py for raise statements and hardcoded
        error strings; verify each is in _CHILD_ERROR_REGISTRY or is
        LLMTransientFailure (special-cased before registry lookup)."""
        raised = _ast_collect_raised_exception_names(_CONVERTERS_CLI)
        # Also collect types from except-handlers (these are caught and
        # re-raised via the broad except Exception handler)
        caught = _ast_collect_except_handler_types(_CONVERTERS_CLI)
        # Filter to only concrete exception names (not generic Exception,
        # BaseException, SystemExit, etc.)
        generic = {"Exception", "BaseException", "SystemExit"}
        all_names = (raised | caught) - generic
        # LLMTransientFailure is classified outside the registry
        all_names.discard("LLMTransientFailure")
        # Standard library exceptions that may propagate but are already
        # handled by non-registry paths in process_document_job
        # (TimeoutError -> converter_timeout, ConverterOOMError -> converter_oom)
        non_registry = {"TimeoutError", "KeyboardInterrupt", "CancelledError"}
        all_names -= non_registry

        registry_keys = set(_CHILD_ERROR_REGISTRY.keys())
        missing = all_names - registry_keys
        assert not missing, (
            f"converters_cli.py can emit these exception class names that are "
            f"NOT in _CHILD_ERROR_REGISTRY: {sorted(missing)}. Add entries or "
            f"confirm they should fall through to _DEFAULT_CHILD_CLASSIFICATION."
        )

    def test_registry_values_are_frozen_dataclass(self):
        """Every value in _CHILD_ERROR_REGISTRY must be a frozen
        ChildErrorClassification dataclass."""
        for key, val in _CHILD_ERROR_REGISTRY.items():
            assert isinstance(val, ChildErrorClassification), (
                f"_CHILD_ERROR_REGISTRY[{key!r}] is {type(val).__name__}, "
                f"expected ChildErrorClassification"
            )
            # frozen=True means __setattr__ raises FrozenInstanceError
            with pytest.raises(AttributeError):
                val.reason = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. _TERMINAL_CHILD_REASONS consistency
# ---------------------------------------------------------------------------

class TestTerminalReasonsExhaustiveness:
    """_TERMINAL_CHILD_REASONS must equal the set of reasons marked terminal
    in the registry, plus llm_failure_terminal (from _classify_llm_failure)."""

    def test_terminal_reasons_match_registry(self):
        registry_terminal = frozenset(
            c.reason for c in _CHILD_ERROR_REGISTRY.values() if c.terminal
        )
        expected = registry_terminal | {"llm_failure_terminal"}
        assert _TERMINAL_CHILD_REASONS == expected, (
            f"Symmetric diff: {_TERMINAL_CHILD_REASONS ^ expected}"
        )

    def test_no_reason_both_terminal_and_transient(self):
        """No reason string should appear as both terminal and non-terminal
        in different registry entries."""
        terminal_reasons = {
            c.reason for c in _CHILD_ERROR_REGISTRY.values() if c.terminal
        }
        transient_reasons = {
            c.reason for c in _CHILD_ERROR_REGISTRY.values() if not c.terminal
        }
        overlap = terminal_reasons & transient_reasons
        assert not overlap, (
            f"Reason(s) classified both terminal and transient: {overlap}"
        )

    def test_llm_classify_terminal_in_terminal_set(self):
        """_classify_llm_failure's terminal output must be in
        _TERMINAL_CHILD_REASONS."""
        terminal_result = _classify_llm_failure("CMap corruption")
        assert terminal_result in _TERMINAL_CHILD_REASONS

    def test_llm_classify_transient_not_in_terminal_set(self):
        """_classify_llm_failure's transient output must NOT be in
        _TERMINAL_CHILD_REASONS."""
        transient_result = _classify_llm_failure("rate limit exceeded")
        assert transient_result not in _TERMINAL_CHILD_REASONS


# ---------------------------------------------------------------------------
# 3. Regression: specific mappings must not change
# ---------------------------------------------------------------------------

class TestChildErrorRegressions:
    """Pin specific classifications that downstream systems depend on."""

    def test_low_quality_tree_is_terminal(self):
        cls = _CHILD_ERROR_REGISTRY["LowQualityTreeError"]
        assert cls.reason == "low_quality_tree"
        assert cls.terminal is True

    def test_runtime_error_is_not_terminal(self):
        cls = _CHILD_ERROR_REGISTRY["RuntimeError"]
        assert cls.reason == "converter_child_failed"
        assert cls.terminal is False

    def test_empty_error_class_uses_default(self):
        """An empty string error_class (child didn't report one) must
        fall through to _DEFAULT_CHILD_CLASSIFICATION."""
        result = _CHILD_ERROR_REGISTRY.get("", _DEFAULT_CHILD_CLASSIFICATION)
        assert result is _DEFAULT_CHILD_CLASSIFICATION
        assert result.terminal is False

    def test_none_error_class_uses_default(self):
        """None error_class must also fall through to default."""
        # In production: _CHILD_ERROR_REGISTRY.get(exc.error_class or "", ...)
        # When error_class is None, ``None or ""`` yields ""
        error_class = None
        result = _CHILD_ERROR_REGISTRY.get(
            error_class or "", _DEFAULT_CHILD_CLASSIFICATION
        )
        assert result is _DEFAULT_CHILD_CLASSIFICATION
        assert result.terminal is False

    def test_unknown_error_class_uses_default(self):
        """A completely unknown exception class name must use default."""
        result = _CHILD_ERROR_REGISTRY.get(
            "SomeNewUnknownError", _DEFAULT_CHILD_CLASSIFICATION
        )
        assert result is _DEFAULT_CHILD_CLASSIFICATION
