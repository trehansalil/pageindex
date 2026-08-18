"""Tests for RFC-023 Task 1.1 (D0): garble-aware ``_text_layer_has_content``.

Validates Design Property 1: for any page text passed to
``_text_layer_has_content``, the function SHALL return ``False`` if the
text is either shorter than 20 characters OR flagged garbled by
``_is_garbled_blob``, and SHALL return ``True`` only for text that is both
long enough AND not garbled.
"""

import types

from pageindex_mcp.converters import _text_layer_has_content

# Repeated single-token blob (>20 alnum tokens, >30% repetition ratio) trips
# _is_garbled_blob's token-repetition check without needing GLYPH</PUA noise.
_GARBLED_TEXT = " ".join(["xkjqz"] * 40)
_CLEAN_TEXT = "This is a perfectly ordinary page of legible English prose. " * 3


def _page(text: str):
    return types.SimpleNamespace(get_text=lambda mode="text": text)


class TestTextLayerHasContent:
    def test_garbled_text_layer_returns_false(self):
        """A text layer long enough to clear the char-count floor but
        flagged garbled (thin mojibake left by the PDF creator) must not be
        treated as real content -- the coverage exemption must still fire."""
        assert _text_layer_has_content(_page(_GARBLED_TEXT)) is False

    def test_clean_text_layer_returns_true(self):
        """A long, non-garbled text layer is real content."""
        assert _text_layer_has_content(_page(_CLEAN_TEXT)) is True

    def test_short_text_returns_false_regardless_of_garble(self):
        """Text at/under the char-count floor returns False even though it
        is not garbled -- the length check short-circuits before the garble
        check runs."""
        assert _text_layer_has_content(_page("short")) is False

    def test_empty_text_returns_false(self):
        assert _text_layer_has_content(_page("")) is False

    def test_garble_check_always_on(self):
        """Zone-4 removed _TEXT_LAYER_GARBLE_CHECK_ENABLED toggle; garble
        check is now always-on — garbled text always returns False."""
        assert _text_layer_has_content(_page(_GARBLED_TEXT)) is False
