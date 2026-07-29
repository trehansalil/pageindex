"""Tests for RFC-023 Task 1.4 (D3): strip ``<!-- ... -->`` HTML comment
markers from garble detection in ``_is_garbled_blob``.

Validates Design Property 4: for any text blob consisting solely of
``<!-- ... -->`` HTML comment markers (regardless of repetition count),
``_is_garbled_blob`` SHALL return ``False``; for any text blob with genuine
repeated non-comment tokens exceeding the 30% threshold, ``_is_garbled_blob``
SHALL still return ``True``.
"""

from pageindex_mcp.helpers import _is_garbled_blob

_IMAGE_MARKER = "<!-- image -->"


class TestImageMarkerGarbleExemption:
    def test_only_image_markers_not_garbled(self):
        """A scanned-PDF markdown with nothing but repeated <!-- image -->
        markers (100% single-token repetition pre-D3) must NOT be flagged
        garbled -- these are structural markers, not mojibake."""
        blob = "\n\n".join([_IMAGE_MARKER] * 45)
        assert _is_garbled_blob(blob) is False

    def test_genuine_repeated_tokens_still_garbled(self):
        """Real repeated-token garble (no HTML comments involved) is
        unaffected by the comment-stripping pre-filter."""
        blob = " ".join(["xkjqz"] * 40)
        assert _is_garbled_blob(blob) is True

    def test_mixed_content_image_markers_excluded_from_repetition_count(self):
        """Real prose interleaved with <!-- image --> markers: the markers
        are stripped before tokenization, so they don't count toward (or
        dilute) the repetition ratio for the surrounding real text."""
        prose = (
            "This is a normal paragraph of legible text describing the "
            "contents of the document in full sentences. "
        )
        blob = f"{prose}\n\n{_IMAGE_MARKER}\n\n{prose}\n\n{_IMAGE_MARKER}\n\n{prose}"
        assert _is_garbled_blob(blob) is False

    def test_other_html_comment_markers_also_exempted(self):
        """The fix generalizes to any <!-- ... --> structural comment, not
        just <!-- image -->, per the RFC's regex-strip design choice."""
        blob = "\n\n".join(["<!-- page-break -->"] * 30)
        assert _is_garbled_blob(blob) is False

    def test_empty_blob_still_garbled(self):
        assert _is_garbled_blob("") is True
