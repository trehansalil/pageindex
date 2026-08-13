"""RFC-029 D0 tests: NFKC normalization + bidi-coherence detection.

Validates:
- Design Property 1: NFKC canonicalization idempotence
- Design Property 2: Bidi-coherence detection
"""
from pageindex_mcp.converters import _pre_inference_normalize, decide_rtl


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
    """Zone-3 consolidation: _check_bidi_coherence was deleted; its sole
    signal was decide_rtl(...).reversed. Tests now use decide_rtl directly."""

    def test_non_arabic_text_returns_coherent(self):
        text = "The quick brown fox jumps over the lazy dog"
        assert not decide_rtl(text).reversed

    def test_healthy_arabic_logical_order_passes(self):
        # Real Arabic in logical (base) codepoints U+0600-06FF only.
        text = "\n".join([
            "السلام عليكم",
            "مرحبا بكم",
            "كيف حالكم",
        ])
        assert not decide_rtl(text).reversed

    def test_visual_order_reversed_arabic_flagged(self):
        # Construct multi-word runs of a character-reversed base Arabic word.
        word = "رارق"
        line = f"{word} {word} {word}"
        text = "\n".join([line, line, line])
        assert decide_rtl(text).reversed

    def test_short_line_ignored(self):
        # Single-word Arabic (fewer than 2 tokens) -- too short to trigger.
        text = "مرحبا"
        assert not decide_rtl(text).reversed

    def test_low_arabic_ratio_line_ignored(self):
        # Mostly ASCII, sparse Arabic -- should not trigger detection.
        text = "hello world foo bar baz qux مر"
        assert not decide_rtl(text).reversed


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
        text = "1234 " * 200
        assert not decide_rtl(text).reversed

    def test_nfkc_does_not_touch_random_bytes(self):
        # Arrange
        text = "".join(chr((i * 37) % 128) for i in range(500))

        # Act
        result = _pre_inference_normalize(text)

        # Assert: ASCII content preserved verbatim (no PF chars → no NFKC)
        assert result == text
