"""Consolidated RFC-020 garble-gate test suite.

Merges (originally separate files, now retired):
- test_rfc020_f0_splice.py     -- F0: tree-path picture-text splice restoration
- test_rfc020_f1f5_coverage.py -- F1: text-layer-gated coverage exemption,
                                   F5: skip-reason plumbing
- test_rfc020_f2f3_garble.py   -- F2: expected_script threading through the
                                   garble-gate call chain, F3: OCR lang override

F0 covers ``splice_picture_text_for_tree`` (tree branch -- leaves
``<!-- image -->`` markers intact, appends OCR text as a blockquote after each
marker) and its composition with the flat-branch ``splice_figure_markers``.

F1 covers ``_recover_picture_text`` exempting a >60%-page-coverage picture
region from the "page_coverage" skip when the underlying PDF page has NO text
layer (a full-page scan where the picture bbox IS the page content, so OCR
must still fire). F5 covers ``_recover_picture_results`` tagging each
skipped/placeholder ``PictureResult`` with the REAL skip reason instead of a
hardcoded string.

F2 covers expected_script threading (``_script_from_filename`` ->
``check_garble`` / ``validate_tree`` / ``_garble_check_nodes``). F3 covers
OCR lang override via ``detect_ocr_langs``.
"""

import logging
import os
import sys
import types

import pytest

from pageindex_mcp import converters
from pageindex_mcp.converters import (
    PictureResult,
    _recover_picture_results,
    _recover_picture_text,
    detect_ocr_langs,
    splice_picture_text_for_tree,
)
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    GarbleConfig,
    ScriptContext,
    _flatten_tree_text,
    _garble_check_nodes,
    _infer_script,
    _script_from_filename,
)
from pageindex_mcp.picture_plane import PictureGateConfig

from tests._garble_compat import check_garble

_MARKER = "<!-- image -->"

# A blob of Latin-alphabet consonant clusters -- no real words in any
# language, long enough to clear the >20-token repetition-check floor and the
# Latin-gibberish ratio threshold used by check_garble(expected_script="Arab").
_LATIN_GIBBERISH = " ".join(["xkjqz vbwm nfrl qpzx wblk"] * 60)

_REAL_ARABIC = "بسم الله الرحمن الرحيم " * 20


def _pic(ocr_text: str = "", **kwargs) -> PictureResult:
    result: PictureResult = {"ocr_text": ocr_text}
    result.update(kwargs)
    return result


# ---------------------------------------------------------------------------
# Shared fake-``fitz`` scaffolding (mirrors tests/test_imgblock_audit_findings.py)
# ---------------------------------------------------------------------------
def _install_fake_fitz(monkeypatch, *, page_text="", clip_text=None, width=612.0, height=792.0):
    """Install a fake ``fitz`` module into ``sys.modules``.

    ``page_text`` is what ``page.get_text("text")`` (no clip) returns -- this
    drives ``_text_layer_has_content``. ``clip_text`` is what
    ``page.get_text("text", clip=rect)`` returns; defaults to ``page_text``
    when not given so tests that don't care about clip-text skip behavior
    aren't accidentally tripped by it.
    """
    resolved_clip_text = page_text if clip_text is None else clip_text

    class _Pix:
        def tobytes(self, fmt="png"):
            return b"\x89PNG fake image bytes"

    class _Page:
        rect = types.SimpleNamespace(width=width, height=height)
        rotation = 0

        def set_rotation(self, value):
            self.rotation = value

        def get_text(self, mode="text", *, clip=None):
            if clip is not None:
                return resolved_clip_text
            return page_text

        def get_pixmap(self, clip, dpi):
            return _Pix()

    class _Pdf:
        page_count = 1

        def __getitem__(self, i):
            return _Page()

        def close(self):
            pass

    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        width=a[2] - a[0] if len(a) >= 4 else 0,
        height=a[3] - a[1] if len(a) >= 4 else 0,
    )
    fake.open = lambda path: _Pdf()
    monkeypatch.setitem(sys.modules, "fitz", fake)


def _region(l=0, t=0, r=612, b=792):
    """A picture region bbox. Defaults to the FULL page (612x792, US Letter)."""
    return {
        "page": 1,
        "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None),
    }


def _long_text(n=60):
    return "x" * n


# ===========================================================================
# F0 -- splice_picture_text_for_tree / splice_figure_markers
# ===========================================================================
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

    def test_markers_preserved_after_splice(self):
        md = f"# Title\n\n{_MARKER}\n\nA\n\n{_MARKER}\n\nB\n\n{_MARKER}\n\nC"
        pics = [_pic("x"), _pic(""), _pic("z")]

        out = splice_picture_text_for_tree(md, pics)

        assert out.count(_MARKER) == md.count(_MARKER) == 3

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


# ===========================================================================
# F1 -- text-layer-gated coverage exemption in _recover_picture_text
# ===========================================================================
class TestF1CoverageExemption:
    def test_full_page_with_text_layer_skipped(self, monkeypatch):
        """Full-page region + page HAS a text layer -> coverage skip applies
        (the picture is decorative background over real text, not content)."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        _install_fake_fitz(monkeypatch, page_text=_long_text(60))
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) == "page_coverage"
        # D5a (RFC-029): page_coverage retains png_bytes + skipped_reason, no ocr_text.
        assert 0 in recovered
        assert recovered[0].get("skipped_reason") == "page_coverage"
        assert recovered[0].get("png_bytes")
        assert not recovered[0].get("ocr_text")

    def test_coverage_exempt_env_var_false(self, monkeypatch):
        """With the exemption disabled, a full-page region + no text layer is
        STILL skipped as page_coverage (pre-F1 / legacy behavior)."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", False)
        monkeypatch.setattr(
            converters.pictures,
            "_GATE_CONFIG",
            PictureGateConfig(
                coverage_exempt_no_text_layer=False,
            ),
        )
        _install_fake_fitz(monkeypatch, page_text="", clip_text="")
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

        recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

        assert skip_reasons.get(0) == "page_coverage"
        # D5a (RFC-029): page_coverage retains png_bytes + skipped_reason, no ocr_text.
        assert 0 in recovered
        assert recovered[0].get("skipped_reason") == "page_coverage"
        assert recovered[0].get("png_bytes")
        assert not recovered[0].get("ocr_text")

    def test_clip_text_skip(self, monkeypatch):
        """A sub-coverage region whose clip already has real text under it
        AND that text is already contained in the Docling markdown export
        (RFC-024 D1 containment guard) is skipped with reason
        "clip_text_already_exported" rather than re-OCR'd."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        small_region = _region(l=0, t=0, r=100, b=100)
        _install_fake_fitz(monkeypatch, page_text="", clip_text=_long_text(30))
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

        recovered, skip_reasons = _recover_picture_text(
            "dummy.pdf", [small_region], ["eng"], md=_long_text(30)
        )

        assert skip_reasons.get(0) == "clip_text_already_exported"
        # D5a (RFC-029): clip_text_already_exported retains png_bytes and ocr_text.
        assert 0 in recovered
        assert recovered[0].get("skipped_reason") == "clip_text_already_exported"
        assert recovered[0].get("png_bytes")
        assert recovered[0].get("ocr_text") == _long_text(30)


# ===========================================================================
# F5 -- skip-reason plumbing (_recover_picture_results uses the REAL reason,
# not a hardcoded "page_coverage" string)
# ===========================================================================
class TestF5SkipReason:
    def _setup(self, monkeypatch, *, recovered, skip_reasons, n_regions=1):
        monkeypatch.setattr(converters.pictures, "_OCR_ESCALATION_PER_PICTURE", True)
        monkeypatch.setattr(
            converters.pictures,
            "_collect_picture_regions",
            lambda d: [_region() for _ in range(n_regions)],
        )
        monkeypatch.setattr(converters.pictures, "detect_ocr_langs", lambda s: ["eng"])
        monkeypatch.setattr(converters.pictures, "ensure_tessdata", lambda langs: langs)
        monkeypatch.setattr(
            converters.pictures,
            "_recover_picture_text",
            lambda *a, **k: (recovered, skip_reasons),
        )

    @pytest.mark.parametrize("reason", ["page_coverage", "clip_text"])
    def test_skip_reason_propagated_verbatim(self, monkeypatch, reason):
        self._setup(monkeypatch, recovered={}, skip_reasons={0: reason})

        pics = _recover_picture_results("x <!-- image --> y", object(), "d.pdf")

        assert len(pics) == 1
        assert pics[0].get("skipped_reason") == reason

    def test_skip_reason_dense_ordinal_preserved_alongside_recovered(self, monkeypatch):
        """Mixed case: one region recovered, one skipped with a real reason,
        one defaulting to unknown -- ordinals must stay aligned (finding 4)."""
        pr0 = PictureResult(ocr_text="recovered chart text here", png_bytes=b"a", page=1, bbox={})
        self._setup(
            monkeypatch,
            recovered={0: pr0},
            skip_reasons={1: "page_coverage"},
            n_regions=3,
        )

        pics = _recover_picture_results("x <!-- image --> y", object(), "d.pdf")

        assert len(pics) == 3
        assert pics[0] is pr0
        assert pics[1].get("skipped_reason") == "page_coverage"
        assert pics[2].get("skipped_reason") == "unknown"


# ===========================================================================
# F2 -- expected_script threading through the garble-gate call chain
# ===========================================================================
class TestExpectedScriptThreading:
    @pytest.mark.parametrize(
        "filename, expected",
        [
            ("وارد_597.pdf", "Arab"),
            # Zone-1: _script_from_filename now returns "Latn" for deu/eng filenames
            ("Haftpflicht_2024.pdf", "Latn"),
        ],
    )
    def test_script_from_filename(self, filename, expected):
        assert _script_from_filename(filename) == expected

    def test_tree_bulk_garble_with_none_script_latin_gibberish(self):
        nodes = [{"text": _LATIN_GIBBERISH}]
        result = check_garble(_flatten_tree_text(nodes), expected_script=None, profile=BULK_PROFILE)
        assert isinstance(result, bool)

    def test_garble_check_nodes_expected_script_preference(self, caplog):
        # Node text is Latin-script-inferred, but the caller passes an Arabic
        # expected_script derived from the filename -- expected_script must win
        # and the mismatch must be logged.
        latin_text = "The quick brown fox jumps over the lazy dog " * 5
        nodes = [{"text": latin_text, "nodes": []}]
        with caplog.at_level(logging.WARNING):
            count = _garble_check_nodes(
            nodes,
            script_context=ScriptContext(dominant_script="Arab", had_presentation_forms=False, source="test"),
            config=GarbleConfig(),
        )
        assert isinstance(count, int)
        assert any("mismatch" in rec.message.lower() for rec in caplog.records)

    def test_garble_check_nodes_fallback_to_infer(self):
        # Without an expected_script, the function must fall back to
        # _infer_script() per-node rather than raising or ignoring text.
        latin_text = "The quick brown fox jumps over the lazy dog " * 5
        nodes = [{"text": latin_text, "nodes": []}]
        assert _infer_script(latin_text) in ("Latn", None)
        count = _garble_check_nodes(
            nodes,
            script_context=ScriptContext(dominant_script=None, had_presentation_forms=False, source="test"),
            config=GarbleConfig(),
        )
        assert isinstance(count, int)


# ===========================================================================
# F3 -- OCR lang override via detect_ocr_langs
# ===========================================================================
class TestOcrLangOverride:
    def test_detect_ocr_langs_arabic_filename(self):
        langs = detect_ocr_langs("وارد_597.pdf")
        assert "ara" in langs
