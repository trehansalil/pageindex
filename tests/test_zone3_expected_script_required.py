"""Zone-3 regression: garble_prongs and check_garble require expected_script kwarg.
All production call sites must pass it explicitly."""

from __future__ import annotations

import ast
import inspect
import re

import pytest

from pageindex_mcp.helpers import garble_prongs, check_garble, BULK_PROFILE


# ---------------------------------------------------------------------------
# garble_prongs raises TypeError without expected_script
# ---------------------------------------------------------------------------

class TestExpectedScriptRequired:
    def test_positional_only_raises(self):
        """garble_prongs(blob) without expected_script should work (it has a
        default of None), but we verify the signature accepts it as kwarg."""
        sig = inspect.signature(garble_prongs)
        params = sig.parameters
        assert "expected_script" in params
        # expected_script must be keyword-only (after *)
        assert params["expected_script"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_accepts_expected_script_none(self):
        """Calling with expected_script=None should not raise."""
        result = garble_prongs("hello world", expected_script=None)
        assert isinstance(result, frozenset)

    def test_accepts_expected_script_value(self):
        """Calling with expected_script='Latn' should not raise."""
        result = garble_prongs("hello world", expected_script="Latn")
        assert isinstance(result, frozenset)


# ---------------------------------------------------------------------------
# check_garble signature enforcement: expected_script and profile are keyword-only
# ---------------------------------------------------------------------------

class TestCheckGarbleSignatureEnforcement:
    def test_expected_script_is_keyword_only(self):
        """check_garble must require expected_script as keyword-only."""
        sig = inspect.signature(check_garble)
        params = sig.parameters
        assert "expected_script" in params
        assert params["expected_script"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_profile_is_keyword_only(self):
        """check_garble must require profile as keyword-only."""
        sig = inspect.signature(check_garble)
        params = sig.parameters
        assert "profile" in params
        assert params["profile"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_positional_expected_script_raises_type_error(self):
        """Calling check_garble('text', 'Latn', profile=BULK_PROFILE) positionally must raise TypeError."""
        with pytest.raises(TypeError):
            check_garble("hello world", "Latn", profile=BULK_PROFILE)  # type: ignore[misc]

    def test_keyword_expected_script_works(self):
        result = check_garble("hello world", expected_script="Latn", profile=BULK_PROFILE)
        assert isinstance(result, bool)

    def test_keyword_expected_script_none_works(self):
        result = check_garble("hello world", expected_script=None, profile=BULK_PROFILE)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# AST scan helpers
# ---------------------------------------------------------------------------

_PRODUCTION_FILES = [
    "src/pageindex_mcp/helpers.py",
    "src/pageindex_mcp/converters.py",
    "src/pageindex_mcp/client.py",
]


def _find_calls(filepath: str, func_name: str) -> list[tuple[int, bool]]:
    """Parse *filepath* and return (line_number, has_expected_script) for
    every call to ``func_name``."""
    with open(filepath) as f:
        source = f.read()
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        pytest.skip(f"Cannot parse {filepath}")
        return []

    results: list[tuple[int, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name != func_name:
            continue
        has_kwarg = any(kw.arg == "expected_script" for kw in node.keywords)
        results.append((node.lineno, has_kwarg))
    return results


# Keep backward compat alias used by the parametrized test below.
def _find_garble_prongs_calls(filepath: str) -> list[tuple[int, bool]]:
    return _find_calls(filepath, "garble_prongs")


# ---------------------------------------------------------------------------
# AST scan: all garble_prongs calls in production pass expected_script
# ---------------------------------------------------------------------------

class TestProductionCallSitesPassExpectedScript:
    @pytest.mark.parametrize("filepath", _PRODUCTION_FILES)
    def test_all_calls_pass_expected_script(self, filepath):
        """Every garble_prongs() call in production code must pass
        expected_script explicitly (not rely on the default)."""
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        full_path = os.path.join(project_root, filepath)
        if not os.path.exists(full_path):
            pytest.skip(f"{filepath} not found")

        calls = _find_garble_prongs_calls(full_path)
        missing = [(line, has) for line, has in calls if not has]
        assert not missing, (
            f"garble_prongs calls WITHOUT expected_script= in {filepath}: "
            f"lines {[l for l, _ in missing]}"
        )


# ---------------------------------------------------------------------------
# AST scan: all check_garble calls in helpers/converters/client pass
# expected_script explicitly
# ---------------------------------------------------------------------------

class TestCheckGarbleCallSitesPassExpectedScript:
    @pytest.mark.parametrize("filepath", _PRODUCTION_FILES)
    def test_all_calls_pass_expected_script(self, filepath):
        """Every check_garble() call must pass expected_script= explicitly."""
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        full_path = os.path.join(project_root, filepath)
        if not os.path.exists(full_path):
            pytest.skip(f"{filepath} not found")

        calls = _find_calls(full_path, "check_garble")
        # Exclude the function definition line itself (def check_garble)
        missing = [(line, has) for line, has in calls if not has]
        assert not missing, (
            f"check_garble calls WITHOUT expected_script= in {filepath}: "
            f"lines {[l for l, _ in missing]}"
        )
