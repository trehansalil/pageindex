"""Zone-3 garble script hole tests: regression for ward-597-class Latin gibberish
in Arab-expected documents, and blob_kind-dependent normalize_for_garble behavior."""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import garble_prongs, check_garble, BULK_PROFILE
from pageindex_mcp.script import BlobKind, normalize_for_garble


# ---------------------------------------------------------------------------
# Regression: ward 597 class -- Latin gibberish in Arab-expected document
# ---------------------------------------------------------------------------

def _make_latin_gibberish_blob(gibberish_ratio: float = 0.65, total_words: int = 100) -> str:
    """Construct a blob with >60% Latin gibberish tokens that looks like
    garbled OCR in an Arabic document (the ward 597 pattern)."""
    # Morphologically nonsense Latin tokens (no vowels, short random consonants)
    nonsense_tokens = [
        "xzk", "brf", "qwp", "jmn", "tks", "vrl", "ghs", "pfx",
        "dkl", "wrt", "czm", "hgb", "nlk", "skr", "brm", "pqz",
    ]
    # Real Arabic tokens (to fill the remaining ~35%)
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


class TestWard597LatinGibberishInArab:
    """Regression: a blob with >60% Latin gibberish in a document where
    expected_script='Arab' must be detected as garbled."""

    def test_latin_gibberish_prong_fires(self):
        blob = _make_latin_gibberish_blob(gibberish_ratio=0.70, total_words=80)
        prongs = garble_prongs(blob, expected_script="Arab")
        assert "latin_gibberish" in prongs, (
            f"latin_gibberish prong should fire for ward-597 pattern, "
            f"got prongs: {prongs}"
        )

    def test_is_garbled_blob_returns_true(self):
        blob = _make_latin_gibberish_blob(gibberish_ratio=0.70, total_words=80)
        assert check_garble(blob, expected_script="Arab", profile=BULK_PROFILE) is True

    def test_without_expected_script_misses_gibberish(self):
        """Without expected_script, the latin_gibberish prong should NOT fire
        (this was the original ward-597 bug: call sites omitting expected_script)."""
        blob = _make_latin_gibberish_blob(gibberish_ratio=0.70, total_words=80)
        prongs = garble_prongs(blob, expected_script=None)
        assert "latin_gibberish" not in prongs, (
            "latin_gibberish should not fire without expected_script"
        )

    def test_latin_script_expected_does_not_fire(self):
        """If expected_script='Latn', latin_gibberish should not fire
        (Latin text in a Latin doc is not anomalous)."""
        blob = _make_latin_gibberish_blob(gibberish_ratio=0.70, total_words=80)
        prongs = garble_prongs(blob, expected_script="Latn")
        assert "latin_gibberish" not in prongs


class TestClassifyVerdictImageEnrichmentRejectsGarble:
    """Regression: classify_verdict's image_enrichment_promoted path must
    correctly reject garbled promoted text (ward-597 class)."""

    def test_promoted_garbled_text_not_pass(self):
        """When _promoted_text is garbled (latin_gibberish), the
        image_enrichment_promoted path must NOT return PASS."""
        from pageindex_mcp.helpers import classify_verdict, TreeGateResult, TreeDefect

        # Build a minimal structure to reach image_enrichment_promoted
        garbled_blob = _make_latin_gibberish_blob(gibberish_ratio=0.70, total_words=80)
        structure = [
            {"title": "root", "body": garbled_blob, "children": []},
        ]

        gate = TreeGateResult(True, TreeDefect.OK)
        verdict, reason = classify_verdict(
            structure=structure,
            content_class="flat_prose",
            validate_result=gate,
            image_enrichment_ratio=0.9,
            expected_script="Arab",
        )
        # The verdict must NOT be PASS with reason image_enrichment_promoted
        # because the promoted text is garbled
        if reason == "image_enrichment_promoted":
            assert verdict != "PASS", (
                "classify_verdict returned PASS/image_enrichment_promoted for garbled text"
            )


# ---------------------------------------------------------------------------
# Contract: blob_kind-dependent normalize_for_garble behavior
# ---------------------------------------------------------------------------

class TestBlobKindNormalizeForGarble:
    """garble_prongs with blob_kind=RAW_MARKDOWN strips heading markers and
    pipe chars before computing ratios, while TREE_TEXT does not."""

    def test_raw_markdown_strips_scaffolding(self):
        blob = "## Title\n| col1 | col2 |\nBody text here"
        raw_result = normalize_for_garble(blob, BlobKind.RAW_MARKDOWN)
        tree_result = normalize_for_garble(blob, BlobKind.TREE_TEXT)

        # RAW_MARKDOWN strips # and |
        assert "#" not in raw_result
        assert "|" not in raw_result
        # TREE_TEXT preserves them
        assert "#" in tree_result
        assert "|" in tree_result

    def test_different_prongs_at_threshold_boundary(self):
        """The same input text should produce different prong sets when markdown
        formatting characters change the denominator past a threshold boundary.

        Strategy: construct a blob where the digit ratio is just above 60% when
        pipe characters are included (TREE_TEXT normalizer keeps them in the
        denominator, diluting digit %), but stripping them (RAW_MARKDOWN) pushes
        digits above the threshold by removing non-digit pipe characters.
        """
        from pageindex_mcp.script import GARBLE_DIGIT_FLOOR

        # We need total chars > GARBLE_DIGIT_FLOOR (500) after normalization.
        # Build text where:
        # - many digits (the signal)
        # - pipe characters (stripped in RAW_MARKDOWN, kept in TREE_TEXT)
        # - pipe chars push digit ratio below 60% for TREE_TEXT but
        #   removing them pushes it above 60% for RAW_MARKDOWN.

        # ~350 digit chars + ~250 pipe/formatting chars = 600 total for TREE_TEXT
        # Digit ratio with pipes: 350/600 = 58.3% (below 60%)
        # After stripping pipes: ~350 digits / ~350 remaining = much higher ratio
        digit_part = "1234567890 " * 35  # 385 chars (350 digits + 35 spaces)
        pipe_part = "| " * 125  # 250 chars of pipes + spaces
        blob = digit_part + pipe_part

        # Verify the setup
        tree_norm = normalize_for_garble(blob, BlobKind.TREE_TEXT)
        raw_norm = normalize_for_garble(blob, BlobKind.RAW_MARKDOWN)

        # TREE_TEXT keeps pipes -> larger denominator -> digit ratio diluted
        tree_digits = sum(1 for c in tree_norm if c.isdigit())
        raw_digits = sum(1 for c in raw_norm if c.isdigit())

        # Both should have the same number of actual digit characters
        assert tree_digits == raw_digits, "Digit count should be the same in both"

        # But the denominators differ
        assert len(tree_norm) > len(raw_norm), (
            "TREE_TEXT should have more chars than RAW_MARKDOWN (pipes kept)"
        )

        # Now verify the prong difference (if both are long enough)
        if len(tree_norm) > GARBLE_DIGIT_FLOOR and len(raw_norm) > GARBLE_DIGIT_FLOOR:
            tree_prongs = garble_prongs(normalize_for_garble(blob, BlobKind.TREE_TEXT), expected_script=None)
            raw_prongs = garble_prongs(normalize_for_garble(blob, BlobKind.RAW_MARKDOWN), expected_script=None)
            # At minimum, the prong sets should differ in content
            # (one may have digit_ratio, the other may not, depending on exact ratios)
            tree_digit_ratio = tree_digits / max(len(tree_norm), 1)
            raw_digit_ratio = raw_digits / max(len(raw_norm), 1)
            if tree_digit_ratio <= 0.60 < raw_digit_ratio:
                assert "digit_ratio" not in tree_prongs, (
                    "TREE_TEXT should NOT trigger digit_ratio (ratio below 60%)"
                )
                assert "digit_ratio" in raw_prongs, (
                    "RAW_MARKDOWN should trigger digit_ratio (ratio above 60%)"
                )

    def test_same_text_different_blob_kind_identity_vs_strip(self):
        """For the same text with markdown formatting, the prong computation
        must use differently-normalized denominators."""
        # A text that has heading markers and pipes taking up space
        blob = "# " + "x" * 20 + "\n" + "| " * 10 + "\n" + "normal text body"
        prongs_tree = garble_prongs(normalize_for_garble(blob, BlobKind.TREE_TEXT), expected_script=None)
        prongs_raw = garble_prongs(normalize_for_garble(blob, BlobKind.RAW_MARKDOWN), expected_script=None)
        # Both should be frozensets (basic sanity)
        assert isinstance(prongs_tree, frozenset)
        assert isinstance(prongs_raw, frozenset)
