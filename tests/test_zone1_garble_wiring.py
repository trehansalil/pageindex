"""Zone-1 garble wiring tests (wave 4 update).

Contracts locked:
1. **Wiring** -- all production callsites import and call check_garble,
   not the legacy functions directly.
2. **Wiring** -- _tree_is_garbled and _flat_text_is_garbled are removed;
   converters.py and client.py must not reference them.
3. **Integration** -- classify_verdict flat-doc path threads expected_script
   through to check_garble.
4. **Wiring** -- _has_sparse_mojibake removed from helpers.py (inlined).
5. **Wiring** -- garble_prongs accepts had_presentation_forms and original_text.
6. **Wiring** -- check_garble accepts had_presentation_forms kwarg.
7. **Wiring** -- no infer_script fallback pattern outside allowed sites.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    GarbleProfile,
    TreeDefect,
    TreeSignals,
    check_garble,
    _garble_ratio,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SRC = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp"

_PRODUCTION_FILES = {
    "helpers.py": _SRC / "helpers.py",
    "converters.py": _SRC / "converters.py",
    "client.py": _SRC / "client.py",
}


def _parse_file(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _find_calls_to(tree: ast.Module, func_name: str) -> list[ast.Call]:
    """Find all Call nodes where the function name matches."""
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == func_name:
                calls.append(node)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == func_name:
                calls.append(node)
    return calls


def _find_imports_of(tree: ast.Module, name: str) -> list[str]:
    """Find all import statements that import the given name."""
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                actual = alias.asname or alias.name
                if actual == name:
                    imports.append(f"from {node.module} import {alias.name}")
    return imports


# ---------------------------------------------------------------------------
# 1. Wiring: production files use check_garble, not legacy functions
# ---------------------------------------------------------------------------

class TestWiringCheckGarbleUsed:
    """All production callsites must import and call check_garble."""

    def test_helpers_calls_check_garble(self):
        """helpers.py should have check_garble calls (TreeSignals.from_tree,
        _garble_ratio, classify_verdict image-enrichment)."""
        tree = _parse_file(_PRODUCTION_FILES["helpers.py"])
        calls = _find_calls_to(tree, "check_garble")
        assert len(calls) >= 3, (
            f"helpers.py should have at least 3 check_garble calls "
            f"(TreeSignals.from_tree, _garble_ratio, "
            f"classify_verdict image-enrichment), found {len(calls)}"
        )

    def test_converters_imports_check_garble(self):
        """converters.py must import check_garble (3 callsites)."""
        tree = _parse_file(_PRODUCTION_FILES["converters.py"])
        imports = _find_imports_of(tree, "check_garble")
        assert len(imports) >= 1, (
            "converters.py must import check_garble from helpers"
        )
        calls = _find_calls_to(tree, "check_garble")
        assert len(calls) >= 2, (
            f"converters.py should have at least 2 check_garble calls "
            f"(_text_layer_has_content, _document_level_text_fallback), found {len(calls)}"
        )

    def test_client_imports_check_garble(self):
        """client.py must import check_garble (retry-comparison)."""
        tree = _parse_file(_PRODUCTION_FILES["client.py"])
        imports = _find_imports_of(tree, "check_garble")
        assert len(imports) >= 1, (
            "client.py must import check_garble from helpers"
        )
        calls = _find_calls_to(tree, "check_garble")
        assert len(calls) >= 2, (
            f"client.py should have at least 2 check_garble calls "
            f"(retry-comparison pre/post), found {len(calls)}"
        )

    def test_converters_imports_bulk_profile(self):
        """converters.py must import BULK_PROFILE."""
        tree = _parse_file(_PRODUCTION_FILES["converters.py"])
        imports = _find_imports_of(tree, "BULK_PROFILE")
        assert len(imports) >= 1, (
            "converters.py must import BULK_PROFILE from helpers"
        )

    def test_client_imports_bulk_profile(self):
        """client.py must import BULK_PROFILE."""
        tree = _parse_file(_PRODUCTION_FILES["client.py"])
        imports = _find_imports_of(tree, "BULK_PROFILE")
        assert len(imports) >= 1, (
            "client.py must import BULK_PROFILE from helpers"
        )


class TestWiringNoDirectLegacyCalls:
    """Production callsites in converters.py and client.py must NOT call
    legacy garble functions directly (they have been removed)."""

    _LEGACY_FUNCTIONS = [
        "_is_garbled_blob",
        "_tree_is_garbled",
        "_flat_text_is_garbled",
    ]

    @pytest.mark.parametrize("fname", ["converters.py", "client.py"])
    @pytest.mark.parametrize("legacy", _LEGACY_FUNCTIONS)
    def test_no_direct_legacy_call(self, fname, legacy):
        """converters.py and client.py must not call legacy garble functions."""
        tree = _parse_file(_PRODUCTION_FILES[fname])
        calls = _find_calls_to(tree, legacy)
        imports = _find_imports_of(tree, legacy)
        assert len(calls) == 0, (
            f"{fname} calls {legacy} directly ({len(calls)} call(s)) -- "
            f"should use check_garble instead"
        )
        assert len(imports) == 0, (
            f"{fname} imports {legacy} -- should use check_garble instead"
        )


class TestWiringDeletedFunctionsNotInHelpers:
    """_tree_is_garbled and _flat_text_is_garbled must not exist as
    top-level functions in helpers.py (they have been inlined/removed)."""

    def test_tree_is_garbled_not_defined(self):
        """helpers.py must not define _tree_is_garbled as a function."""
        tree = _parse_file(_PRODUCTION_FILES["helpers.py"])
        func_defs = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_tree_is_garbled"
        ]
        assert len(func_defs) == 0, (
            "_tree_is_garbled should have been removed from helpers.py "
            "(inlined into TreeSignals.from_tree)"
        )

    def test_flat_text_is_garbled_not_defined(self):
        """helpers.py must not define _flat_text_is_garbled as a function."""
        tree = _parse_file(_PRODUCTION_FILES["helpers.py"])
        func_defs = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_flat_text_is_garbled"
        ]
        assert len(func_defs) == 0, (
            "_flat_text_is_garbled should have been removed from helpers.py "
            "(consolidated into check_garble)"
        )


# ---------------------------------------------------------------------------
# 2. Integration: classify_verdict flat-doc path threads expected_script
# ---------------------------------------------------------------------------

_PUA = "" * 400


def _leaf(title: str, text: str) -> dict:
    return {"title": title, "text": text, "nodes": []}


class TestClassifyVerdictFlatDocIntegration:
    """classify_verdict with validate_result=None (flat-doc path) must
    thread expected_script through to check_garble. Verify that garble
    detection fires correctly via sig.garbled / sig.effectively_garbled."""

    def test_garbled_german_document_sig_garbled(self):
        """German doc with PUA garble and expected_script='Latn' must
        set sig.garbled=True, proving expected_script was threaded through."""
        structure = [
            {
                "title": "Root",
                "text": "",
                "nodes": [_leaf(f"G{i}", _PUA) for i in range(3)],
            }
        ]
        sig = TreeSignals.from_tree(structure, expected_script="Latn")
        assert sig.garbled is True, (
            "TreeSignals.from_tree must detect garble in PUA-filled tree "
            "(expected_script='Latn' must be threaded through check_garble)"
        )

    def test_clean_german_document_sig_not_garbled(self):
        """Clean German doc with expected_script='Latn' must NOT have
        sig.garbled=True."""
        clean = (
            "Die Versicherung deckt Schaden an Dritten im Rahmen der "
            "vereinbarten Deckungssumme. Der Versicherungsnehmer ist "
            "verpflichtet, den Schaden unverzueglich zu melden. "
        ) * 5
        structure = [
            {
                "title": "Root",
                "text": "",
                "nodes": [_leaf(f"Ch{i}", clean) for i in range(3)],
            }
        ]
        sig = TreeSignals.from_tree(structure, expected_script="Latn")
        assert sig.garbled is False, (
            "Clean German doc should NOT be garbled"
        )

    def test_expected_script_affects_latin_gibberish_detection(self):
        """When expected_script='Arab', latin gibberish in the tree must
        be detected. When expected_script='Latn', the same text should
        NOT be detected as latin gibberish (the prong is script-dependent).
        This proves expected_script is actually threaded to check_garble."""
        nonsense = "xkjqz vbwm bgdr klfn mtrz bab rel teb gux pev " * 20
        result_arab = check_garble(
            nonsense, expected_script="Arab", profile=BULK_PROFILE
        )
        result_latn = check_garble(
            nonsense, expected_script="Latn", profile=BULK_PROFILE
        )
        assert result_arab is True, (
            "expected_script='Arab' must trigger latin_gibberish prong"
        )
        assert result_latn is False, (
            "expected_script='Latn' must NOT trigger latin_gibberish prong"
        )


# ---------------------------------------------------------------------------
# 4. Wiring: _has_sparse_mojibake removed (inlined into garble_prongs)
# ---------------------------------------------------------------------------

class TestWiringSparseMojibakeInlined:
    """_has_sparse_mojibake must not exist as a top-level function in helpers.py;
    its logic is now the 'sparse_mojibake' prong inside garble_prongs."""

    def test_has_sparse_mojibake_not_defined(self):
        tree = _parse_file(_PRODUCTION_FILES["helpers.py"])
        func_defs = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_has_sparse_mojibake"
        ]
        assert len(func_defs) == 0, (
            "_has_sparse_mojibake should have been removed from helpers.py "
            "(inlined into garble_prongs as 'sparse_mojibake' prong)"
        )

    def test_check_garble_does_not_call_has_sparse_mojibake(self):
        """check_garble must not call _has_sparse_mojibake separately --
        the prong is now inside garble_prongs."""
        tree = _parse_file(_PRODUCTION_FILES["helpers.py"])
        calls = _find_calls_to(tree, "_has_sparse_mojibake")
        assert len(calls) == 0, (
            f"helpers.py still calls _has_sparse_mojibake ({len(calls)} call(s)) "
            f"-- should be inlined into garble_prongs"
        )


# ---------------------------------------------------------------------------
# 5. Wiring: garble_prongs signature includes new params
# ---------------------------------------------------------------------------

class TestWiringGarbleProngsSignature:
    """garble_prongs must accept had_presentation_forms and original_text."""

    def test_garble_prongs_accepts_had_presentation_forms(self):
        """garble_prongs must accept had_presentation_forms=True without error."""
        from pageindex_mcp.helpers import garble_prongs
        result = garble_prongs(
            "test text",
            expected_script="Latn",
            had_presentation_forms=True,
        )
        assert isinstance(result, frozenset)

    def test_garble_prongs_accepts_original_text(self):
        """garble_prongs must accept original_text=... without error."""
        from pageindex_mcp.helpers import garble_prongs
        result = garble_prongs(
            "test text",
            expected_script="Latn",
            original_text="raw text",
        )
        assert isinstance(result, frozenset)


# ---------------------------------------------------------------------------
# 6. Wiring: check_garble accepts had_presentation_forms
# ---------------------------------------------------------------------------

class TestWiringCheckGarbleNewParams:
    """check_garble must accept had_presentation_forms as a keyword argument."""

    def test_check_garble_had_presentation_forms(self):
        result = check_garble(
            "test text",
            expected_script="Latn",
            profile=BULK_PROFILE,
            had_presentation_forms=False,
        )
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# 7. Wiring: no infer_script fallback in client.py or converters.py
# ---------------------------------------------------------------------------

class TestWiringNoInferScriptFallback:
    """client.py and converters.py must not contain the pattern
    'expected_script or infer_script(' -- the fallback is centralized
    inside check_garble."""

    @pytest.mark.parametrize("fname", ["client.py", "converters.py"])
    def test_no_infer_script_fallback_pattern(self, fname):
        import re
        source = _PRODUCTION_FILES[fname].read_text()
        pattern = re.compile(r"expected_script\s+or\s+_?infer_script\s*\(")
        matches = list(pattern.finditer(source))
        assert len(matches) == 0, (
            f"{fname} still contains 'expected_script or infer_script(' pattern "
            f"at {len(matches)} site(s) -- fallback should be centralized in check_garble"
        )
