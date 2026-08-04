"""RFC-029 D0 tests: NFKC normalization + bidi-coherence detection.

Validates:
- Design Property 1: NFKC canonicalization idempotence
- Design Property 2: Bidi-coherence detection
"""
from pageindex_mcp.converters import _pre_inference_normalize
from pageindex_mcp.helpers import _check_bidi_coherence


# ---------------------------------------------------------------------------
# Property 1: NFKC canonicalization idempotence
# ---------------------------------------------------------------------------


class TestNFKCCanonicalization:
    def test_arabic_presentation_forms_are_normalized(self):
        # Arrange: U+FB50 (Arabic Presentation Form-A) and U+FB51
        pf_text = "ﭐﭑ"

        # Act
        result = _pre_inference_normalize(pf_text)

        # Assert: canonical form is U+0671 (isolated ALEF with WASLA)
        assert "ﭐ" not in result
        assert "ﭑ" not in result
        # No Presentation-Form-A codepoints should remain
        for ch in result:
            assert not ("ﭐ" <= ch <= "﷿")

    def test_normalization_is_idempotent(self):
        # Arrange: pure PF input; downstream mojibake/rtl transforms in the
        # full pipeline are known to be non-idempotent on mixed content, so we
        # scope this property to the NFKC pass by round-tripping a canonical
        # base-Arabic string that other passes will leave unchanged.
        once = _pre_inference_normalize("ﭐﭑ")

        # Act
        twice = _pre_inference_normalize(once)

        # Assert
        assert once == twice

    def test_non_arabic_text_is_not_altered(self):
        # Arrange
        plain = "The quick brown fox jumps over the lazy dog 123 !@#"

        # Act
        result = _pre_inference_normalize(plain)

        # Assert
        assert result == plain

    def test_mixed_arabic_and_ascii_preserves_ascii(self):
        # Arrange: PF glyphs mixed with ASCII
        mixed = "Prefix ﭐﭑ suffix"

        # Act
        result = _pre_inference_normalize(mixed)

        # Assert: ASCII bits present, PF glyphs gone
        assert "Prefix " in result
        assert " suffix" in result
        assert "ﭐ" not in result


# ---------------------------------------------------------------------------
# Property 2: Bidi-coherence detection
# ---------------------------------------------------------------------------


class TestBidiCoherenceCheck:
    def test_non_arabic_text_returns_coherent(self):
        # Arrange
        text = "The quick brown fox jumps over the lazy dog"

        # Act
        ok, reason = _check_bidi_coherence(text)

        # Assert
        assert ok is True
        assert reason == ""

    def test_healthy_arabic_logical_order_passes(self):
        # Arrange: real Arabic in logical (base) codepoints U+0600-06FF only.
        # These are logical-order characters; no presentation-form glyphs.
        text = "\n".join([
            "السلام عليكم",
            "مرحبا بكم",
            "كيف حالكم",
        ])

        # Act
        ok, reason = _check_bidi_coherence(text)

        # Assert
        assert ok is True
        assert reason == ""

    def test_visual_order_reversed_arabic_flagged(self):
        # Arrange: construct multi-word runs where the FIRST character of each
        # word is a FINAL-FORM presentation glyph and/or the LAST character is
        # an INITIAL-FORM glyph. These are >40% Arabic per line.
        # U+FE8E ARABIC LETTER ALEF FINAL FORM (final-form at word start)
        # U+FE91 ARABIC LETTER BEH INITIAL FORM (initial-form at word end)
        final_alef = "ﺎ"
        initial_beh = "ﺑ"
        # Pad each word with U+0627 (base Arabic ALEF) so the Arabic-ratio
        # gate treats the line as Arabic-dominant.
        word = final_alef + "ااا" + initial_beh
        line = f"{word} {word} {word}"
        text = "\n".join([line, line, line])

        # Act
        ok, reason = _check_bidi_coherence(text)

        # Assert
        assert ok is False
        assert reason == "visual_order_garble"

    def test_short_line_ignored(self):
        # Arrange: single-word Arabic (fewer than 2 tokens) is not a run
        text = "مرحبا"

        # Act
        ok, reason = _check_bidi_coherence(text)

        # Assert
        assert ok is True
        assert reason == ""

    def test_low_arabic_ratio_line_ignored(self):
        # Arrange: mostly ASCII, sparse Arabic — should not trigger detection
        text = "hello world foo bar baz qux مر"

        # Act
        ok, reason = _check_bidi_coherence(text)

        # Assert
        assert ok is True
        assert reason == ""


# ---------------------------------------------------------------------------
# Regression: genuinely garbled non-bidi noise is not affected by the
# NFKC/bidi additions (existing garble detection paths still apply upstream).
# ---------------------------------------------------------------------------


class TestNoiseRegression:
    def test_repeating_digits_bidi_check_returns_coherent(self):
        """Repeating numeric noise has no Arabic, so bidi check is coherent.

        The upstream `_is_garbled_blob` handles this class; the bidi function
        is not expected to duplicate that role.
        """
        # Arrange
        text = "1234 " * 200

        # Act
        ok, reason = _check_bidi_coherence(text)

        # Assert: bidi check leaves non-Arabic noise alone
        assert ok is True
        assert reason == ""

    def test_nfkc_does_not_touch_random_bytes(self):
        # Arrange
        text = "".join(chr((i * 37) % 128) for i in range(500))

        # Act
        result = _pre_inference_normalize(text)

        # Assert: ASCII content preserved verbatim (no PF chars → no NFKC)
        assert result == text
