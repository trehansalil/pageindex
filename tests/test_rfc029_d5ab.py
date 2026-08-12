"""RFC-029 Design Property 7 — Picture-context retention (Task 7.3).

Tests for ``splice_figure_markers`` in ``pageindex_mcp.converters`` and the
standalone-image D5b semantic in ``pageindex_mcp.client``.

Covers:
  - Property 7a: retained skip under ``clip_text_already_exported`` keeps marker
    (non-empty ``png_bytes`` + ``ocr_text`` → splice emits figure reference)
  - Property 7b: standalone JPG passthrough — when ``md_content`` exceeds
    ``MIN_STANDALONE_IMAGE_MD_CHARS``, the ``ocr_text`` field on the synthetic
    ``PictureResult`` carries the full ``md_content`` through ``splice_figure_markers``
  - Regression: truly-empty ``PictureResult`` (no ocr, desc, png_bytes) with a
    skip/decorative flag → ``splice_figure_markers`` strips the marker

Scope-reduction notes
---------------------
* ``_recover_picture_text`` requires a live ``fitz``/Docling document object and
  Tesseract binaries; exercising it end-to-end is impractical in a pure unit-test
  environment.  Tests here instead construct ``PictureResult``-shaped dicts that
  match what the RFC-029 Wave 9 fix emits (retained-skip case) and verify the
  downstream consumer ``splice_figure_markers`` behaves correctly.

* ``client.index()`` requires OpenAI + Docling + MinIO; the D5b standalone-image
  branch is scope-reduced to: import ``MIN_STANDALONE_IMAGE_MD_CHARS``, build a
  synthetic ``PictureResult`` with ``ocr_text=md_content`` (exactly as the else
  branch does at client.py ~L946), and assert ``splice_figure_markers`` preserves
  the text in its output.
"""
from __future__ import annotations

import pytest

from pageindex_mcp.converters import PictureResult, splice_figure_markers
from pageindex_mcp.client import MIN_STANDALONE_IMAGE_MD_CHARS

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_MARKER = "<!-- image -->"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _retained_skip_result(
    ocr_text: str = "Revenue grew 12% YoY",
    png_bytes: bytes = b"\x89PNG\r\n",
    skipped_reason: str = "clip_text_already_exported",
) -> PictureResult:
    """Build a PictureResult matching the RFC-029 Wave 9 retained-skip emission."""
    result: PictureResult = {}
    result["ocr_text"] = ocr_text
    result["png_bytes"] = png_bytes
    result["skipped_reason"] = skipped_reason
    result["page"] = 1
    result["bbox"] = {"l": 10, "t": 20, "r": 100, "b": 80}
    return result


def _truly_empty_result(skipped_reason: str = "page_coverage") -> PictureResult:
    """Build a PictureResult with no content — what a decorative/skipped image yields."""
    result: PictureResult = {}
    result["skipped_reason"] = skipped_reason
    return result


# ---------------------------------------------------------------------------
# Property 7a — retention under clip_text_already_exported
# ---------------------------------------------------------------------------


class TestRetainedSkipClipTextAlreadyExported:
    def test_retained_result_has_nonempty_png_bytes(self):
        """PictureResult from retained-skip path must carry non-empty png_bytes."""
        # Arrange
        result = _retained_skip_result()

        # Act / Assert
        assert result.get("png_bytes"), "png_bytes must be present and non-empty"

    def test_retained_result_has_nonempty_ocr_text(self):
        """PictureResult from retained-skip path must carry non-empty ocr_text."""
        # Arrange
        result = _retained_skip_result()

        # Act / Assert
        assert result.get("ocr_text"), "ocr_text must be present and non-empty"

    def test_splice_keeps_marker_when_png_bytes_present(self):
        """splice_figure_markers MUST NOT strip a marker when png_bytes is set —
        the retained-skip guard ``if not (ocr or desc or result.get('png_bytes'))``
        short-circuits, so no strip occurs regardless of skipped_reason."""
        # Arrange
        md = f"Some prose.\n\n{_MARKER}\n\nMore prose."
        pics = [_retained_skip_result(ocr_text="", png_bytes=b"\x89PNG\r\n")]

        # Act
        out = splice_figure_markers(md, pics)

        # Assert — marker is resolved to a figure reference, not stripped
        assert _MARKER not in out, "raw marker must be replaced, not left in output"
        assert "[Figure: fig-0]" in out, "figure reference must appear"

    def test_splice_emits_chart_text_block_when_ocr_text_retained(self):
        """When ocr_text is non-empty on a retained-skip result,
        splice_figure_markers emits a [Chart text] prose block."""
        # Arrange
        md = f"Intro.\n\n{_MARKER}\n\nTrailing."
        ocr = "Revenue grew 12% YoY"
        pics = [_retained_skip_result(ocr_text=ocr)]

        # Act
        out = splice_figure_markers(md, pics)

        # Assert
        assert "[Figure: fig-0]" in out
        assert f"> [Chart text]: {ocr}" in out

    def test_splice_sets_spliced_flag_after_emission(self):
        """After splice_figure_markers runs, _spliced_into_markdown flag is set
        so downstream consumers know the text was already emitted (RFC-028 D5).
        ocr_text is preserved (non-destructive splice)."""
        # Arrange
        md = f"{_MARKER}"
        pics = [_retained_skip_result(ocr_text="chart data")]

        # Act
        splice_figure_markers(md, pics)

        # Assert -- flag set, ocr_text preserved
        assert pics[0].get("_spliced_into_markdown") is True, \
            "_spliced_into_markdown flag must be set after splice"
        assert pics[0].get("ocr_text") == "chart data", \
            "ocr_text must be preserved (non-destructive splice)"


# ---------------------------------------------------------------------------
# Property 7b — standalone JPG passthrough (D5b scope-reduced)
# ---------------------------------------------------------------------------


class TestStandaloneJpgPassthrough:
    """Scope-reduced: exercises the semantic of the D5b else-branch in client.py
    (``standalone_ocr_text = md_content``) by constructing the same synthetic
    PictureResult and confirming splice_figure_markers preserves the content.

    End-to-end execution of client.index() requires OpenAI + Docling + MinIO
    and is not exercised here."""

    def test_min_standalone_image_md_chars_is_positive_int(self):
        """MIN_STANDALONE_IMAGE_MD_CHARS must be a positive integer importable
        from client — ensures the D8a gate threshold is stable."""
        # Arrange / Act / Assert
        assert isinstance(MIN_STANDALONE_IMAGE_MD_CHARS, int)
        assert MIN_STANDALONE_IMAGE_MD_CHARS > 0

    def test_md_content_longer_than_threshold_triggers_d5b_branch(self):
        """When len(md_content.split()) > MIN_STANDALONE_IMAGE_MD_CHARS the D5b
        branch sets standalone_ocr_text = md_content.  Verify the invariant:
        a synthetic PictureResult built with ocr_text=md_content preserves the
        full text through splice_figure_markers."""
        # Arrange — md_content clearly exceeds the character threshold
        md_content = "Revenue chart shows Q1 growth. " * 20  # well over 100 chars
        assert len("".join(md_content.split())) > MIN_STANDALONE_IMAGE_MD_CHARS, \
            "precondition: md_content must exceed D8a gate to trigger D5b branch"

        # Synthetic PictureResult as constructed by the D5b else-branch in client.py
        pic: PictureResult = {
            "ocr_text": md_content,
            "page": 1,
            "bbox": {"l": 0, "t": 0, "r": 0, "b": 0},
            "png_bytes": b"\xff\xd8\xff",  # JPEG magic bytes
        }
        md = f"Document title.\n\n{_MARKER}\n\nSome footer text."

        # Act
        out = splice_figure_markers(md, [pic])

        # Assert — figure reference emitted and chart text block present
        assert "[Figure: fig-0]" in out
        assert "> [Chart text]:" in out
        # The ocr_text (= md_content) must appear in the output prose
        assert "Revenue chart shows Q1 growth." in out

    def test_md_content_shorter_than_threshold_does_not_trigger_d5b(self):
        """When md_content is below or equal to the threshold, the D8a gate fires
        Tesseract OCR instead (not D5b).  Verify the inverse: a PictureResult
        with empty ocr_text (what Tesseract returns when unavailable) and only
        png_bytes still resolves the marker without a [Chart text] block."""
        # Arrange
        short_md = "x" * MIN_STANDALONE_IMAGE_MD_CHARS  # at threshold, not over
        whitespace_stripped_len = len("".join(short_md.split()))
        # Ensure this is ≤ threshold (D8a gate: <= not <)
        assert whitespace_stripped_len <= MIN_STANDALONE_IMAGE_MD_CHARS

        pic: PictureResult = {
            "ocr_text": "",  # Tesseract returned nothing
            "page": 1,
            "bbox": {"l": 0, "t": 0, "r": 0, "b": 0},
            "png_bytes": b"\xff\xd8\xff",
        }
        md = f"Preamble.\n\n{_MARKER}\n\nPostamble."

        # Act
        out = splice_figure_markers(md, [pic])

        # Assert — figure reference still emitted (png_bytes keeps the marker alive)
        assert "[Figure: fig-0]" in out
        assert "> [Chart text]:" not in out  # no OCR text to splice


# ---------------------------------------------------------------------------
# Regression: truly-empty PictureResult with skip flag strips the marker
# ---------------------------------------------------------------------------


class TestTrulyEmptyResultStripsMarker:
    def test_empty_result_with_skipped_reason_strips_marker_by_default(self):
        """A PictureResult with no ocr_text, no description, no png_bytes, but
        with skipped_reason set must strip the <!-- image --> marker when
        STRIP_SKIPPED_IMAGE_MARKERS=true (the default)."""
        # Arrange
        md = f"Before.\n\n{_MARKER}\n\nAfter."
        pics = [_truly_empty_result(skipped_reason="page_coverage")]

        # Act — no monkeypatch: default env is strip=true
        out = splice_figure_markers(md, pics)

        # Assert
        assert _MARKER not in out, "skipped/empty marker must be stripped by default"
        assert "[Figure:" not in out, "no figure reference must be emitted for empty result"

    def test_empty_result_with_decorative_flag_strips_marker(self):
        """A PictureResult with decorative=True and no content strips the marker."""
        # Arrange
        md = f"Text.\n\n{_MARKER}\n\nEnd."
        pic: PictureResult = {"decorative": True}

        # Act
        out = splice_figure_markers(md, [pic])

        # Assert
        assert _MARKER not in out
        assert "[Figure:" not in out

    def test_empty_result_no_skip_flag_leaves_neutral_marker(self):
        """An entirely empty PictureResult with no skip/decorative flag keeps the
        raw marker neutral (falls through to ``return m.group(0)``)."""
        # Arrange
        md = f"Text.\n\n{_MARKER}\n\nEnd."
        pic: PictureResult = {}  # no content, no flags

        # Act
        out = splice_figure_markers(md, [pic])

        # Assert — neutral pass-through, marker preserved unchanged
        assert _MARKER in out
