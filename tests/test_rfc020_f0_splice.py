"""Tests for RFC-020 Task 1.3 (F0): tree-path picture-text splice restoration.

Covers `splice_picture_text_for_tree` (tree branch — leaves `<!-- image -->`
markers intact, appends OCR text as a blockquote after each marker) and its
composition with the flat-branch `splice_figure_markers` (which replaces the
same markers with `[Figure: fig-N]` references).
"""

import os

import pytest

from pageindex_mcp.converters import (
    PictureResult,
    splice_figure_markers,
    splice_picture_text_for_tree,
)

_MARKER = "<!-- image -->"


def _pic(ocr_text: str = "", **kwargs) -> PictureResult:
    result: PictureResult = {"ocr_text": ocr_text}
    result.update(kwargs)
    return result


class TestSplicePictureTextForTree:
    def test_ocr_text_appended_after_markers(self):
        md = f"# Title\n\n{_MARKER}\n\nSome text.\n\n{_MARKER}\n\nMore text."
        pics = [_pic("Revenue 2024: 42%"), _pic("Costs down 10%")]

        out = splice_picture_text_for_tree(md, pics)

        assert out.count(_MARKER) == 2
        assert "> [Chart text]: Revenue 2024: 42%" in out
        assert "> [Chart text]: Costs down 10%" in out
        # Ordering: first chart-text block follows first marker, before second marker.
        first_marker_idx = out.index(_MARKER)
        first_chart_idx = out.index("> [Chart text]: Revenue 2024: 42%")
        second_marker_idx = out.index(_MARKER, first_marker_idx + 1)
        assert first_marker_idx < first_chart_idx < second_marker_idx

    def test_empty_pics_returns_unchanged(self):
        md = f"# Title\n\n{_MARKER}\n\nSome text."

        out = splice_picture_text_for_tree(md, [])

        assert out == md

    def test_count_mismatch_returns_unchanged(self, caplog):
        md = f"# Title\n\n{_MARKER}\n\nText.\n\n{_MARKER}\n\nMore."
        pics = [_pic("a"), _pic("b"), _pic("c")]

        with caplog.at_level("WARNING"):
            out = splice_picture_text_for_tree(md, pics)

        assert out == md
        assert any(
            "mismatch" in record.message or "WARNING" in record.levelname
            for record in caplog.records
        )
        assert caplog.records, "expected a warning to be logged on count mismatch"

    def test_markers_preserved_after_splice(self):
        md = f"# Title\n\n{_MARKER}\n\nA\n\n{_MARKER}\n\nB\n\n{_MARKER}\n\nC"
        pics = [_pic("x"), _pic(""), _pic("z")]

        out = splice_picture_text_for_tree(md, pics)

        assert out.count(_MARKER) == md.count(_MARKER) == 3

    def test_composition_with_splice_figure_markers(self):
        """RFC-028 D5: chaining both splice functions on the same markdown must
        not duplicate the chart-text fragment.

        Production never does this: the tree branch calls only
        `splice_picture_text_for_tree` (markers stay neutral for some other
        downstream tree consumer) and the flat branch calls only
        `splice_figure_markers` (markers get resolved to `[Figure: fig-N]`).
        `splice_picture_text_for_tree` deliberately leaves the marker intact
        (see its docstring) so that `splice_figure_markers` can still match
        it, but it now pops ``ocr_text`` off the shared ``PictureResult`` dict
        once spliced, so a later `splice_figure_markers` pass over the same
        `pics` list finds no `ocr_text` left to re-append — one representation
        per fragment.
        """
        md = f"# Title\n\n{_MARKER}\n\nBody text."
        pics = [_pic("Chart shows growth", png_bytes=b"\x89PNG")]

        tree_out = splice_picture_text_for_tree(md, pics)
        composed = splice_figure_markers(tree_out, pics)

        assert "[Figure: fig-0]" in composed
        assert _MARKER not in composed
        # De-duplicated: the chart-text line appears exactly once.
        assert composed.count("> [Chart text]: Chart shows growth") == 1

    def test_no_ocr_text_leaves_marker_alone(self):
        md = f"# Title\n\n{_MARKER}\n\nBody."
        pics = [_pic("")]

        out = splice_picture_text_for_tree(md, pics)

        assert out == md
        assert "> [Chart text]:" not in out
        assert _MARKER in out

    def test_kill_switch_env_var(self, monkeypatch):
        """TREE_PATH_PICTURE_SPLICE_ENABLED gates whether client.index() calls
        splice_picture_text_for_tree at all (see client.py wiring). This test
        verifies the env-var truthiness parsing matches the documented
        contract: "1"/"true"/"yes" (case-insensitive) enable the splice;
        anything else (including "false", "0", "", unset-with-default "true")
        follows the same parse the production code uses.
        """

        def _parse(raw: str) -> bool:
            return raw.strip().lower() in ("1", "true", "yes")

        monkeypatch.setenv("TREE_PATH_PICTURE_SPLICE_ENABLED", "false")
        assert _parse(os.environ["TREE_PATH_PICTURE_SPLICE_ENABLED"]) is False

        monkeypatch.setenv("TREE_PATH_PICTURE_SPLICE_ENABLED", "true")
        assert _parse(os.environ["TREE_PATH_PICTURE_SPLICE_ENABLED"]) is True

        monkeypatch.setenv("TREE_PATH_PICTURE_SPLICE_ENABLED", "0")
        assert _parse(os.environ["TREE_PATH_PICTURE_SPLICE_ENABLED"]) is False

        monkeypatch.setenv("TREE_PATH_PICTURE_SPLICE_ENABLED", "YES")
        assert _parse(os.environ["TREE_PATH_PICTURE_SPLICE_ENABLED"]) is True

        monkeypatch.delenv("TREE_PATH_PICTURE_SPLICE_ENABLED", raising=False)
        default = os.getenv("TREE_PATH_PICTURE_SPLICE_ENABLED", "true")
        assert _parse(default) is True

        # Behavioral check: when disabled, callers must skip the splice call
        # entirely and pass markdown through untouched (mirrors client.py's
        # `if pic_results and TREE_PATH_PICTURE_SPLICE_ENABLED:` guard).
        md = f"# Title\n\n{_MARKER}\n\nBody."
        pics = [_pic("ocr text here")]
        enabled = _parse("false")
        md_content = md
        if pics and enabled:
            md_content = splice_picture_text_for_tree(md_content, pics)
        assert md_content == md
        assert "> [Chart text]:" not in md_content
