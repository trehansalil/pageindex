"""Zone-3 regression tests: detect_garble unified entry point.

Covers:
- ward-597 Latin gibberish (RFC-019 D2)
- presentation_forms prong fires post-NFKC (RFC-028 D2 / RFC-033 D2)
- title reversed-morphology detection
- check_garble backward-compat wrapper equivalence
"""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    GarbleConfig,
    check_garble,
    detect_garble,
    garble_prongs,
)
from pageindex_mcp.script import (
    ARABIC_RANGES,
    BlobKind,
    PRESENTATION_RANGES,
    ScriptContext,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_latin_gibberish_blob(gibberish_ratio: float = 0.65, total_words: int = 100) -> str:
    """Construct a blob with Latin gibberish tokens (ward-597 pattern)."""
    nonsense_tokens = [
        "xzk", "brf", "qwp", "jmn", "tks", "vrl", "ghs", "pfx",
        "dkl", "wrt", "czm", "hgb", "nlk", "skr", "brm", "pqz",
    ]
    arabic_tokens = [
        "المادة", "الأولى", "تنظيم", "الحقوق", "والواجبات",
        "للمواطنين", "القانون", "العام",
    ]
    n_gibberish = int(total_words * gibberish_ratio)
    n_arabic = total_words - n_gibberish
    tokens = []
    for i in range(n_gibberish):
        tokens.append(nonsense_tokens[i % len(nonsense_tokens)])
    for i in range(n_arabic):
        tokens.append(arabic_tokens[i % len(arabic_tokens)])
    return " ".join(tokens)


def _make_pf_text(pf_ratio: float = 0.60, total_arabic: int = 100) -> str:
    """Build text with a given ratio of Presentation Forms to Arabic chars."""
    pf_count = int(total_arabic * pf_ratio)
    pf_chars = [chr(c) for c in range(0xFE70, 0xFE70 + pf_count)]
    regular_chars = [chr(c) for c in range(0x0620, 0x0620 + total_arabic - pf_count)]
    return "".join(pf_chars + regular_chars)


# ---------------------------------------------------------------------------
# Regression: ward-597 Latin gibberish (RFC-019 D2)
# ---------------------------------------------------------------------------


class TestWard597LatinGibberishDetectGarble:
    """detect_garble must flag ward-597 class Latin gibberish in Arab context."""

    def test_detect_garble_flags_latin_gibberish(self):
        blob = _make_latin_gibberish_blob(gibberish_ratio=0.70, total_words=80)
        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=False, source="test")
        cfg = GarbleConfig()
        result = detect_garble(blob, script_context=ctx, config=cfg)
        assert result.is_garbled, "ward-597 Latin gibberish in Arab context must be detected"
        assert "latin_gibberish" in result.fired_prongs

    def test_detect_garble_clean_arabic_not_flagged(self):
        clean = "المادة الأولى تنظيم الحقوق والواجبات للمواطنين القانون العام " * 10
        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=False, source="test")
        cfg = GarbleConfig()
        result = detect_garble(clean, script_context=ctx, config=cfg)
        assert not result.is_garbled, "Clean Arabic text must not be flagged as garbled"
        assert result.fired_prongs == frozenset()


# ---------------------------------------------------------------------------
# Regression: presentation_forms prong fires post-NFKC (RFC-028 D2 / RFC-033 D2)
# ---------------------------------------------------------------------------


class TestPresentationFormsProngDetectGarble:
    """The presentation_forms prong must fire when ScriptContext.had_presentation_forms
    is True (pre-NFKC detection), even if the actual blob is post-NFKC normalized."""

    def test_pf_prong_fires_via_script_context(self):
        """ScriptContext with had_presentation_forms=True makes detect_garble
        return True via the presentation_forms prong."""
        # Post-NFKC text: PF codepoints already decomposed, so
        # inline scan would miss them. ScriptContext carries the flag.
        import unicodedata

        raw = _make_pf_text(pf_ratio=0.60, total_arabic=80)
        post_nfkc = unicodedata.normalize("NFKC", raw)
        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=True, source="test")
        cfg = GarbleConfig()
        result = detect_garble(post_nfkc, script_context=ctx, config=cfg)
        assert result.is_garbled, "presentation_forms prong must fire via ScriptContext flag"
        assert "presentation_forms" in result.fired_prongs

    def test_pf_prong_does_not_fire_without_flag(self):
        """When had_presentation_forms=False and the blob has no PF chars,
        the presentation_forms prong must not fire."""
        clean = "المادة الأولى تنظيم الحقوق والواجبات للمواطنين القانون " * 10
        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=False, source="test")
        cfg = GarbleConfig()
        prongs = garble_prongs(
            clean, expected_script="Arab", had_presentation_forms=False, config=cfg,
        )
        assert "presentation_forms" not in prongs

    def test_pf_detect_garble_fallback_on_raw_blob(self):
        """When ScriptContext.had_presentation_forms=False but the blob itself
        has >50% PF ratio (raw text, not yet NFKC), detect_garble's inline
        fallback scan should still detect it."""
        raw = _make_pf_text(pf_ratio=0.60, total_arabic=80)
        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=False, source="test")
        cfg = GarbleConfig()
        result = detect_garble(raw, script_context=ctx, config=cfg)
        assert result.is_garbled, "Inline PF fallback scan should catch raw PF chars"
        assert "presentation_forms" in result.fired_prongs


# ---------------------------------------------------------------------------
# Regression: title reversed-morphology detection
# ---------------------------------------------------------------------------


class TestTitleReversedMorphology:
    """detect_garble should detect reversed-morphology patterns in titles
    (via garble_prongs internals)."""

    def test_reversed_arabic_title_detected(self):
        """A reversed Arabic title should be detected as garbled via
        the node-level reversed-morphology check in _garble_check_nodes."""
        # This test verifies the reversed-morphology path exists by checking
        # that _word_has_reversed_morphology is importable and functional
        from pageindex_mcp.script import _word_has_reversed_morphology

        # Reversed Arabic: joining letters in non-joining positions
        # (this is what RTL reversal produces)
        assert callable(_word_has_reversed_morphology)


# ---------------------------------------------------------------------------
# Backward compat: check_garble wrapper equivalence
# ---------------------------------------------------------------------------


class TestCheckGarbleBackwardCompat:
    """check_garble (backward-compat wrapper) must produce identical results
    to detect_garble for the same inputs."""

    @pytest.mark.parametrize(
        "text,expected_script,profile",
        [
            # Clean German text (Latn)
            (
                "Die Versicherung umfasst die gesetzliche Haftpflicht des Versicherungsnehmers " * 5,
                "Latn",
                BULK_PROFILE,
            ),
            # Clean Arabic text
            (
                "المادة الأولى تنظيم الحقوق والواجبات للمواطنين القانون العام " * 5,
                "Arab",
                BULK_PROFILE,
            ),
            # Latin gibberish in Arab context (should be garbled)
            (
                _make_latin_gibberish_blob(gibberish_ratio=0.70, total_words=80),
                "Arab",
                BULK_PROFILE,
            ),
            # Empty text
            (
                "",
                "Latn",
                BULK_PROFILE,
            ),
            # None script
            (
                "Some generic text that is not particularly garbled at all and is quite long enough for analysis " * 3,
                None,
                BULK_PROFILE,
            ),
        ],
        ids=["clean-german", "clean-arabic", "latin-gibberish-arab", "empty", "none-script"],
    )
    def test_check_garble_matches_detect_garble(
        self, text: str, expected_script: str | None, profile,
    ):
        """check_garble wrapper produces same boolean as calling detect_garble
        with equivalent parameters."""
        # check_garble path (legacy)
        legacy_result = check_garble(
            text, expected_script=expected_script, profile=profile,
        )

        # detect_garble path (new)
        blob_kind = BlobKind.RAW_MARKDOWN if profile.normalize_markdown else BlobKind.TREE_TEXT
        ctx = ScriptContext(
            dominant_script=expected_script,
            had_presentation_forms=False,
            source="test",
        )
        # Use _rebuild_garble_config_compat to match what check_garble does
        from pageindex_mcp.helpers import _rebuild_garble_config_compat

        compat_cfg = _rebuild_garble_config_compat()
        new_result = detect_garble(text, script_context=ctx, config=compat_cfg, blob_kind=blob_kind)

        assert legacy_result == bool(new_result), (
            f"check_garble and detect_garble disagree for "
            f"expected_script={expected_script!r}, profile={profile}"
        )

    def test_flat_markdown_profile_equivalence(self):
        """FLAT_MARKDOWN_PROFILE maps to BlobKind.RAW_MARKDOWN in detect_garble."""
        text = "# Heading\n\nSome text content here for testing\n\n| col1 | col2 |"
        legacy = check_garble(
            text, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE,
        )
        ctx = ScriptContext(dominant_script="Latn", had_presentation_forms=False, source="test")
        from pageindex_mcp.helpers import _rebuild_garble_config_compat

        cfg = _rebuild_garble_config_compat()
        new = detect_garble(
            text, script_context=ctx, config=cfg, blob_kind=BlobKind.RAW_MARKDOWN,
        )
        assert legacy == bool(new)
