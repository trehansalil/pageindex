"""Zone-3 RTL consolidation tests: verify six Arabic/RTL deciders are deleted
and decide_rtl is the sole RTL decision point. Verify _pre_inference_normalize
idempotence and validate_tree single-call contract."""

from __future__ import annotations

import ast
import os
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Project root helper
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Exhaustiveness: six legacy RTL functions are deleted
# ---------------------------------------------------------------------------

_DELETED_NAMES = [
    "_detect_arabic_reversal",
    "_text_is_logical_order",
    "_heading_is_logical_order",
    "_fix_residual_rtl_reversal",
    "_tree_is_rtl_reversed",
    "_check_bidi_coherence",
]


class TestDeletedRtlFunctions:
    """Each of the six legacy RTL decision functions must be deleted from
    both helpers.py and converters.py (ImportError or AttributeError on access)."""

    @pytest.mark.parametrize("name", _DELETED_NAMES)
    def test_not_importable_from_helpers(self, name):
        import pageindex_mcp.helpers as helpers
        assert not hasattr(helpers, name), (
            f"{name} should be deleted from helpers.py but is still present"
        )

    @pytest.mark.parametrize("name", _DELETED_NAMES)
    def test_not_importable_from_converters(self, name):
        import pageindex_mcp.converters as converters
        assert not hasattr(converters, name), (
            f"{name} should be deleted from converters.py but is still present"
        )

    @pytest.mark.parametrize("name", _DELETED_NAMES)
    def test_not_importable_from_script(self, name):
        import pageindex_mcp.script as script
        assert not hasattr(script, name), (
            f"{name} should be deleted from script.py but is still present"
        )


# ---------------------------------------------------------------------------
# Exhaustiveness: decide_rtl is the sole RTL decision point
# ---------------------------------------------------------------------------

class TestDecideRtlIsSoleDecisionPoint:
    def test_converters_imports_decide_rtl(self):
        """converters.py must import decide_rtl from script."""
        from pageindex_mcp.converters import decide_rtl
        from pageindex_mcp.script import decide_rtl as script_decide_rtl
        assert decide_rtl is script_decide_rtl

    def test_helpers_imports_decide_rtl(self):
        """helpers.py must import decide_rtl from script."""
        from pageindex_mcp.helpers import decide_rtl
        from pageindex_mcp.script import decide_rtl as script_decide_rtl
        assert decide_rtl is script_decide_rtl

    def test_no_other_rtl_decision_functions_in_converters(self):
        """converters.py must not define any of the deleted RTL deciders."""
        filepath = os.path.join(_PROJECT_ROOT, "src/pageindex_mcp/converters.py")
        with open(filepath) as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
        func_defs = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in _DELETED_NAMES:
            assert name not in func_defs, (
                f"{name} is still DEFINED in converters.py -- must be deleted"
            )

    def test_no_other_rtl_decision_functions_in_helpers(self):
        """helpers.py must not define any of the deleted RTL deciders."""
        filepath = os.path.join(_PROJECT_ROOT, "src/pageindex_mcp/helpers.py")
        with open(filepath) as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
        func_defs = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in _DELETED_NAMES:
            assert name not in func_defs, (
                f"{name} is still DEFINED in helpers.py -- must be deleted"
            )


# ---------------------------------------------------------------------------
# Exhaustiveness: _inject_arabic_structural_headings uses decide_rtl
# ---------------------------------------------------------------------------

class TestInjectArabicHeadingsUsesDecideRtl:
    def test_inject_arabic_calls_decide_rtl(self):
        """_inject_arabic_structural_headings must detect reversed OCR via decide_rtl."""
        filepath = os.path.join(_PROJECT_ROOT, "src/pageindex_mcp/converters.py")
        with open(filepath) as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)

        # Find the _inject_arabic_structural_headings function
        inject_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_inject_arabic_structural_headings":
                inject_func = node
                break
        assert inject_func is not None, "_inject_arabic_structural_headings not found in converters.py"

        # Walk its body for calls to decide_rtl
        found_decide_rtl = False
        for node in ast.walk(inject_func):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name == "decide_rtl":
                    found_decide_rtl = True
                    break
        assert found_decide_rtl, (
            "_inject_arabic_structural_headings must call decide_rtl for reversed OCR detection"
        )


# ---------------------------------------------------------------------------
# Regression: _pre_inference_normalize calls reconstruct_bidi_order exactly once
# ---------------------------------------------------------------------------

class TestPreInferenceNormalizeSingleBidiCall:
    def test_calls_reconstruct_bidi_order_once(self):
        """_pre_inference_normalize must call reconstruct_bidi_order exactly once
        (not twice via the deleted _fix_residual_rtl_reversal)."""
        filepath = os.path.join(_PROJECT_ROOT, "src/pageindex_mcp/converters.py")
        with open(filepath) as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)

        # Find _pre_inference_normalize
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_pre_inference_normalize":
                func_node = node
                break
        assert func_node is not None

        # Count calls to reconstruct_bidi_order in its body
        call_count = 0
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name == "reconstruct_bidi_order":
                    call_count += 1
        assert call_count == 1, (
            f"_pre_inference_normalize calls reconstruct_bidi_order {call_count} times, expected 1"
        )

    def test_does_not_call_fix_residual_rtl(self):
        """_pre_inference_normalize must NOT call _fix_residual_rtl_reversal (deleted)."""
        filepath = os.path.join(_PROJECT_ROOT, "src/pageindex_mcp/converters.py")
        with open(filepath) as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)

        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_pre_inference_normalize":
                func_node = node
                break
        assert func_node is not None

        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                assert name != "_fix_residual_rtl_reversal", (
                    "_pre_inference_normalize still calls the deleted _fix_residual_rtl_reversal"
                )

    def test_idempotence(self):
        """Applying _pre_inference_normalize twice yields the same result as once."""
        from pageindex_mcp.converters import _pre_inference_normalize

        # Latin text
        latin = "## Heading\n\nSome body text with content."
        once = _pre_inference_normalize(latin)
        twice = _pre_inference_normalize(once)
        assert twice == once, "Latin text: _pre_inference_normalize is not idempotent"

        # Arabic text (logical order)
        arabic = "## المادة الأولى\n\nتنظيم الحقوق والواجبات للمواطنين"
        once_ar = _pre_inference_normalize(arabic)
        twice_ar = _pre_inference_normalize(once_ar)
        assert twice_ar == once_ar, "Arabic text: _pre_inference_normalize is not idempotent"

        # Mixed text
        mixed = "## Title\n\nالمادة الأولى\n\nSome English text.\n\nتنظيم الحقوق"
        once_mix = _pre_inference_normalize(mixed)
        twice_mix = _pre_inference_normalize(once_mix)
        assert twice_mix == once_mix, "Mixed text: _pre_inference_normalize is not idempotent"

        # Empty text
        assert _pre_inference_normalize("") == ""
        assert _pre_inference_normalize(_pre_inference_normalize("")) == ""


# ---------------------------------------------------------------------------
# Contract: validate_tree single decide_rtl call + BIDI_COHERENCE_ENFORCE
# ---------------------------------------------------------------------------

class TestValidateTreeSingleDecideRtl:
    def test_validate_tree_calls_decide_rtl_once(self):
        """validate_tree must call decide_rtl exactly once on flat_text,
        reusing the result for both RTL_REVERSAL and BIDI_DEGRADED gates."""
        filepath = os.path.join(_PROJECT_ROOT, "src/pageindex_mcp/helpers.py")
        with open(filepath) as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)

        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "validate_tree":
                func_node = node
                break
        assert func_node is not None

        # Count decide_rtl calls in validate_tree body
        call_count = 0
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name == "decide_rtl":
                    call_count += 1
        assert call_count == 1, (
            f"validate_tree calls decide_rtl {call_count} times, expected exactly 1"
        )

    def test_validate_tree_references_both_gates(self):
        """validate_tree must reference both RTL_REVERSAL and BIDI_DEGRADED
        defect enums (the single decide_rtl result feeds both gates)."""
        filepath = os.path.join(_PROJECT_ROOT, "src/pageindex_mcp/helpers.py")
        with open(filepath) as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)

        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "validate_tree":
                func_node = node
                break
        assert func_node is not None

        # Unparse the function body and check for both references
        body_source = ast.get_source_segment(source, func_node)
        assert "RTL_REVERSAL" in body_source, "validate_tree must reference RTL_REVERSAL"
        assert "BIDI_DEGRADED" in body_source, "validate_tree must reference BIDI_DEGRADED"

    def test_bidi_coherence_enforce_env_var_gates_bidi_degraded(self, monkeypatch):
        """BIDI_COHERENCE_ENFORCE must gate the BIDI_DEGRADED gate function.

        Zone-1 moved the gate bodies out of validate_tree into GATE_TABLE, so
        the env var now lives in ``_gate_bidi_degraded``.  This is checked
        behaviourally rather than by source inspection: previously the env var
        appeared only in a comment, which the source-inspection assertion
        could not distinguish from real gating.
        """
        from pageindex_mcp.helpers import GATE_TABLE, TreeDefect, _gate_bidi_degraded

        # The gate is wired into the table under the BIDI_DEGRADED defect.
        assert (_gate_bidi_degraded, TreeDefect.BIDI_DEGRADED) in GATE_TABLE

        reversed_decision = MagicMock()
        reversed_decision.reversed = True
        args = (MagicMock(), [], None, None, reversed_decision)

        monkeypatch.setenv("BIDI_COHERENCE_ENFORCE", "true")
        fires, _ = _gate_bidi_degraded(*args)
        assert fires is True, "gate must fire when enforcement is enabled"

        monkeypatch.setenv("BIDI_COHERENCE_ENFORCE", "false")
        fires, _ = _gate_bidi_degraded(*args)
        assert fires is False, "BIDI_COHERENCE_ENFORCE=false must disable the gate"
