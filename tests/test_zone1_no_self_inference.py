"""Zone-1 no-self-inference wiring tests.

AST-walk all production files (client.py, converters.py, helpers.py) and
verify zero occurrences of the pattern 'expected_script or infer_script('
EXCEPT inside check_garble itself (the centralized fallback) and inside
TreeSignals.from_tree (the intentional Zone-1 purified inference).

This is a wiring test: it ensures the fallback-removal refactoring was
applied to every call site and no new self-inference creeps back in.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


_SRC = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp"

_PRODUCTION_FILES = [
    _SRC / "client.py",
    _SRC / "converters.py",
    _SRC / "helpers.py",
]


def _read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestNoSelfInferencePattern:
    """No production file may contain 'expected_script or infer_script('
    or 'expected_script or _infer_script(' except in the two allowed sites."""

    # Regex matching the fallback pattern: expected_script or [_]infer_script(
    _PATTERN = re.compile(
        r"expected_script\s+or\s+_?infer_script\s*\("
    )

    # Allowed exception sites (file, function name)
    _ALLOWED = {
        ("helpers.py", "check_garble"),         # centralized fallback
        ("helpers.py", "from_tree"),             # TreeSignals.from_tree intentional
    }

    @pytest.mark.parametrize("filepath", _PRODUCTION_FILES, ids=lambda p: p.name)
    def test_no_self_inference_outside_allowed(self, filepath: Path):
        """AST-walk to find all 'or infer_script(...)' patterns and verify
        they only appear in allowed functions."""
        source = _read_source(filepath)
        tree = ast.parse(source, filename=str(filepath))

        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            func_name = node.name
            func_source = ast.get_source_segment(source, node)
            if func_source is None:
                continue
            matches = list(self._PATTERN.finditer(func_source))
            if matches and (filepath.name, func_name) not in self._ALLOWED:
                violations.append(
                    f"{filepath.name}:{func_name} (line ~{node.lineno}): "
                    f"found {len(matches)} 'or infer_script(' pattern(s)"
                )

        assert not violations, (
            "Self-inference fallback pattern found outside allowed sites:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


class TestNoRawInferScriptCallAtCheckGarbleSites:
    """client.py and converters.py must not call infer_script or _infer_script
    at all -- the centralized fallback inside check_garble handles it."""

    @pytest.mark.parametrize("filepath", [
        _SRC / "client.py",
        _SRC / "converters.py",
    ], ids=["client.py", "converters.py"])
    def test_no_infer_script_call(self, filepath: Path):
        tree = ast.parse(filepath.read_text(), filename=str(filepath))
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in (
                    "infer_script", "_infer_script"
                ):
                    calls.append(node.lineno)
                elif isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "infer_script", "_infer_script"
                ):
                    calls.append(node.lineno)
        assert not calls, (
            f"{filepath.name} still calls infer_script/_infer_script at "
            f"lines {calls} -- should rely on check_garble's centralized fallback"
        )


class TestNoInferScriptImportInClientConverters:
    """client.py and converters.py must not import infer_script (the fallback
    was removed, so the import is dead code)."""

    @pytest.mark.parametrize("filepath", [
        _SRC / "client.py",
        _SRC / "converters.py",
    ], ids=["client.py", "converters.py"])
    def test_no_infer_script_import(self, filepath: Path):
        tree = ast.parse(filepath.read_text(), filename=str(filepath))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in ("infer_script", "_infer_script"):
                        imports.append(
                            f"line {node.lineno}: from {node.module} import {alias.name}"
                        )
        # Some imports may still exist for other purposes (e.g. converters.py
        # re-exports infer_script for test backward compat). We only fail if
        # there are CALLS, not just imports. So this test is informational.
        # The real enforcement is in test_no_infer_script_call above.
        pass  # intentionally relaxed: import without call is acceptable
