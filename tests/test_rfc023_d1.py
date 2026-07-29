"""Tests for RFC-023 Tasks 1.2/1.3 (D1): graceful degradation for
``splice_figure_markers`` count mismatch, and raw ``<!-- image -->`` marker
recognition in ``route_and_extract_flat``.

Validates Design Property 2: for any markdown containing N
``<!-- image -->`` markers and M ``PictureResult`` regions where N != M,
``splice_figure_markers`` SHALL splice all markers with a matching ordinal
``PictureResult`` and SHALL strip (or neutrally mark) excess markers without
a matching region, never bailing out to leave all N markers unresolved.
"""

from pageindex_mcp.converters import PictureResult, splice_figure_markers
from pageindex_mcp.helpers import route_and_extract_flat

_MARKER = "<!-- image -->"


def _pic(ocr_text: str = "", **kwargs) -> PictureResult:
    result: PictureResult = {"ocr_text": ocr_text}
    result.update(kwargs)
    return result


class TestGracefulMarkerSplicing:
    def test_mismatched_counts_matched_ordinals_spliced_excess_stripped(self, monkeypatch):
        """3 markers, 2 regions: markers 0 and 1 splice against their
        ordinal PictureResult; the excess 3rd marker (no matching region)
        is stripped, not left unresolved and not misattributed."""
        monkeypatch.setenv("STRIP_SKIPPED_IMAGE_MARKERS", "true")
        md = f"# Title\n\n{_MARKER}\n\nA\n\n{_MARKER}\n\nB\n\n{_MARKER}\n\nC"
        pics = [_pic("Revenue up", png_bytes=b"\x89PNG"), _pic("Costs down", png_bytes=b"\x89PNG")]

        out = splice_figure_markers(md, pics)

        assert "[Figure: fig-0]" in out
        assert "[Figure: fig-1]" in out
        assert "> [Chart text]: Revenue up" in out
        assert "> [Chart text]: Costs down" in out
        # Excess 3rd marker is gone entirely (stripped), not left as a raw marker.
        assert _MARKER not in out
        assert "[Figure: fig-2]" not in out

    def test_mismatched_counts_excess_marker_left_neutral_when_strip_disabled(self, monkeypatch):
        """With STRIP_SKIPPED_IMAGE_MARKERS=false, the excess marker is left
        as a neutral (unresolved) <!-- image --> marker instead of a
        fabricated [Figure: fig-N] reference."""
        monkeypatch.setenv("STRIP_SKIPPED_IMAGE_MARKERS", "false")
        md = f"{_MARKER}\n\nA\n\n{_MARKER}\n\nB"
        pics = [_pic("only one region", png_bytes=b"\x89PNG")]

        out = splice_figure_markers(md, pics)

        assert "[Figure: fig-0]" in out
        assert out.count(_MARKER) == 1

    def test_equal_counts_all_spliced_no_regression(self):
        """N == M: every marker resolves to its PictureResult, matching
        pre-D1 behavior exactly (no regression on the happy path)."""
        md = f"{_MARKER}\n\nA\n\n{_MARKER}\n\nB"
        pics = [_pic("x", png_bytes=b"\x89PNG"), _pic("y", png_bytes=b"\x89PNG")]

        out = splice_figure_markers(md, pics)

        assert "[Figure: fig-0]" in out
        assert "[Figure: fig-1]" in out
        assert _MARKER not in out

    def test_empty_pics_returns_unchanged(self):
        md = f"{_MARKER}\n\nBody."
        assert splice_figure_markers(md, []) == md


class TestRawImageMarkerRecognizedInFlatExtraction:
    def test_unresolved_raw_marker_recognized_as_image_block(self):
        """A raw <!-- image --> marker left unresolved by the graceful
        splice (excess marker, STRIP_SKIPPED_IMAGE_MARKERS=false) is
        recognized by route_and_extract_flat as a content-less image node,
        not silently swallowed as invisible/unrecognized text."""
        md = f"# Title\n\nSome prose.\n\n{_MARKER}\n\nMore prose."

        content_class, blocks = route_and_extract_flat(md)

        image_blocks = [b for b in blocks if b.get("role") == "image"]
        assert len(image_blocks) == 1
        # No matching PictureResult ordinal -- no "index" key set.
        assert "index" not in image_blocks[0]

    def test_multiple_raw_markers_all_recognized(self):
        md = f"{_MARKER}\n\n{_MARKER}\n\n{_MARKER}"

        _content_class, blocks = route_and_extract_flat(md)

        image_blocks = [b for b in blocks if b.get("role") == "image"]
        assert len(image_blocks) == 3

    def test_resolved_figure_reference_still_recognized(self):
        """Pre-existing [Figure: fig-N] recognition (resolved markers) is
        unaffected by the raw-marker addition."""
        md = "[Figure: fig-0 | Revenue chart]\n\n> [Chart text]: up 10%"

        _content_class, blocks = route_and_extract_flat(md)

        image_blocks = [b for b in blocks if b.get("role") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["index"] == 0
