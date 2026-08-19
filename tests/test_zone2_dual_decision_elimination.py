"""Zone-2 dual decision elimination tests (AST-verified).

Validates:
1. client.py has exactly ONE decide_ocr_mode/decide_ocr_strategy call site
   (post-conversion only); the pre-conversion hardcoded-False site is eliminated.
2. converters.py's independent per-picture gating call (decide_ocr_mode in
   _recover_picture_results) remains -- correct by design since it operates
   on post-conversion markdown with real has_image_markers.
"""
from __future__ import annotations

import ast
import inspect
import re
import textwrap

import pytest


# ---------------------------------------------------------------------------
# 1. client.py: single post-conversion decision point
# ---------------------------------------------------------------------------


class TestClientSingleDecisionPoint:
    """client.py must have exactly ONE decide_ocr_strategy call and ZERO
    pre-conversion decide_ocr_mode calls."""

    def test_decide_ocr_strategy_call_count_in_client(self):
        """client.py must call decide_ocr_strategy exactly once
        (post-conversion in _convert_to_tree)."""
        import pageindex_mcp.client as client_mod

        src_path = inspect.getfile(client_mod)
        with open(src_path) as f:
            src = f.read()
        # Count non-import, non-comment calls to decide_ocr_strategy
        calls = []
        for i, line in enumerate(src.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("import") or stripped.startswith("from"):
                continue
            if "decide_ocr_strategy(" in stripped:
                calls.append((i, stripped))
        assert len(calls) == 1, (
            f"Expected exactly 1 decide_ocr_strategy call in client.py, "
            f"found {len(calls)}: {calls}"
        )

    def test_no_pre_conversion_decide_ocr_mode_call(self):
        """client.py must NOT call decide_ocr_mode (the backward-compat
        wrapper) -- it should use decide_ocr_strategy directly."""
        import pageindex_mcp.client as client_mod

        src_path = inspect.getfile(client_mod)
        with open(src_path) as f:
            src = f.read()
        # Look for decide_ocr_mode( calls that are NOT imports
        for i, line in enumerate(src.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("import") or stripped.startswith("from"):
                continue
            if "decide_ocr_mode(" in stripped:
                pytest.fail(
                    f"client.py still calls decide_ocr_mode at line {i}: "
                    f"{stripped!r} -- should use decide_ocr_strategy"
                )

    def test_post_conversion_site_uses_real_has_image_markers(self):
        """The decide_ocr_strategy call in _convert_to_tree must derive
        has_image_markers from actual md_content (not hardcoded False)."""
        from pageindex_mcp.client import CustomPageIndexClient

        src = inspect.getsource(CustomPageIndexClient._convert_to_tree)
        # Find the decide_ocr_strategy call
        tree = ast.parse(textwrap.dedent(src))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name == "decide_ocr_strategy":
                    # Check has_image_markers kwarg is NOT a Constant(False)
                    for kw in node.keywords:
                        if kw.arg == "has_image_markers":
                            # Must not be a bare False literal
                            assert not (
                                isinstance(kw.value, ast.Constant)
                                and kw.value.value is False
                            ), (
                                "has_image_markers is still hardcoded to False "
                                "in the post-conversion decide_ocr_strategy call"
                            )
                            return
        pytest.fail("decide_ocr_strategy call with has_image_markers not found")

    def test_no_hardcoded_false_has_image_markers(self):
        """No decide_ocr_mode/decide_ocr_strategy call in client.py should
        pass has_image_markers=False as a literal."""
        import pageindex_mcp.client as client_mod

        src_path = inspect.getfile(client_mod)
        with open(src_path) as f:
            src = f.read()
        # Simple text search: has_image_markers=False as a literal in
        # non-comment lines
        for i, line in enumerate(src.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "has_image_markers=False" in stripped and "decide_" in stripped:
                pytest.fail(
                    f"client.py hardcodes has_image_markers=False at line {i}: "
                    f"{stripped!r}"
                )


# ---------------------------------------------------------------------------
# 2. converters.py: per-picture gating call retained (correct by design)
# ---------------------------------------------------------------------------


class TestConvertersDecideOcrModeRetained:
    """converters.py's decide_ocr_mode call in _recover_picture_results
    is correct by design and must be retained."""

    def test_decide_ocr_mode_in_recover_picture_results(self):
        """_recover_picture_results must still call decide_ocr_mode."""
        from pageindex_mcp.converters import _recover_picture_results

        src = inspect.getsource(_recover_picture_results)
        assert "decide_ocr_mode(" in src, (
            "_recover_picture_results no longer calls decide_ocr_mode"
        )

    def test_converters_imports_decide_ocr_mode(self):
        """converters.py must import decide_ocr_mode."""
        import pageindex_mcp.converters as conv_mod

        src_path = inspect.getfile(conv_mod)
        with open(src_path) as f:
            src = f.read()
        # decide_ocr_mode may appear on its own line inside a multi-line
        # from-import block.  Check that the symbol name appears at all in
        # a region that also contains 'import' or 'from'.
        assert "decide_ocr_mode" in src, (
            "converters.py does not reference decide_ocr_mode at all"
        )
        # Verify it is actually used (called) in the module, not just imported
        assert "decide_ocr_mode(" in src, (
            "converters.py imports but never calls decide_ocr_mode"
        )

    def test_converters_decide_ocr_mode_uses_real_markers(self):
        """The decide_ocr_mode call in _recover_picture_results must use
        the real has_image_markers check (not hardcoded)."""
        from pageindex_mcp.converters import _recover_picture_results

        src = inspect.getsource(_recover_picture_results)
        tree = ast.parse(textwrap.dedent(src))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name == "decide_ocr_mode":
                    for kw in node.keywords:
                        if kw.arg == "has_image_markers":
                            assert not (
                                isinstance(kw.value, ast.Constant)
                                and kw.value.value is False
                            ), (
                                "has_image_markers hardcoded to False in "
                                "_recover_picture_results decide_ocr_mode call"
                            )
                            return
        pytest.fail("decide_ocr_mode call with has_image_markers not found in _recover_picture_results")


# ---------------------------------------------------------------------------
# 3. Import discipline
# ---------------------------------------------------------------------------


class TestImportDiscipline:
    """client.py imports decide_ocr_strategy from picture_plane;
    converters.py imports decide_ocr_mode from picture_plane."""

    def test_client_imports_decide_ocr_strategy(self):
        import pageindex_mcp.client as client_mod

        src_path = inspect.getfile(client_mod)
        with open(src_path) as f:
            src = f.read()
        assert "decide_ocr_strategy" in src, (
            "client.py does not reference decide_ocr_strategy"
        )
        # Verify it is actually called (not just imported)
        assert "decide_ocr_strategy(" in src, (
            "client.py has decide_ocr_strategy but never calls it"
        )

    def test_client_still_imports_decide_ocr_mode_for_compat(self):
        """client.py may still import decide_ocr_mode (used in tests via
        wiring assertions) -- this is acceptable."""
        import pageindex_mcp.client as client_mod

        src_path = inspect.getfile(client_mod)
        with open(src_path) as f:
            src = f.read()
        # decide_ocr_mode import is acceptable (not required to be removed)
        # but decide_ocr_mode( CALL must not exist (tested elsewhere)
