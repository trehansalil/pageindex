"""RFC-021 QF3: garble-gate bilingual false-positive fix tests.

Covers:
  1. Bilingual Arabic+English text must NOT be flagged garbled.
  2. Actually garbled text (PUA chars, null bytes, GLYPH markers,
     consonant-cluster gibberish) MUST still be detected.
  3. Genuine sparse mojibake (Arabic-Latin-Arabic fragments from
     encoding corruption) still flagged.
  4. Regression: existing garble detection test cases still pass.
"""

import logging

from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    _flatten_tree_text,
    _garble_check_nodes,
    garble_prongs,
    _infer_script,
    _is_morphologically_nonsense,
    check_garble,
    validate_tree,
)


# ── Test data ────────────────────────────────────────────────────────────────

_BILINGUAL_ARABIC_ENGLISH = (
    "هذه اتفاقية مستوى الخدمة Service Level Agreement "
    "تحدد معايير الأداء performance metrics "
    "ومستويات التوفر availability targets "
    "للبنية التحتية infrastructure services "
    "المقدمة بموجب هذا العقد contract "
    "لضمان compliance والامتثال للمعايير الدولية "
    "وتحقيق maintenance standards المطلوبة "
    "بما يشمل bandwidth و latency requirements "
    "وفقا لسياسات provider المعتمدة "
    "مع مراعاة customer obligations "
    "وشروط termination و liability المنصوص عليها "
    "في هذا الاتفاق المبرم بين الطرفين المتعاقدين"
)

_PURE_ARABIC = "بسم الله الرحمن الرحيم " * 20

_PURE_ENGLISH = (
    "The provider shall maintain 99.9% availability for all production "
    "infrastructure services during the contract period. "
    "Service Level Agreement compliance requires quarterly reviews "
    "of performance metrics including bandwidth and latency targets."
)

_GARBLED_LATIN_IN_ARABIC = (
    "هذه اتفاقية مستوى الخدمة xKjQ7 mZpR3 vBnL8 "
    "تحدد معايير الأداء wQxR5 yTnM2 "
    "ومستويات التوفر kLpZ9 jHnW4 "
    "للبنية التحتية rXmQ3 bKvN6 "
    "المقدمة pLzW8 qNxR1 fTmK7 "
    "وتحقيق gRbX4 hMzP2 المطلوبة "
    "بما يشمل cJvQ5 و dLxN9 requirements "
    "وفقا لسياسات nWpK3 المعتمدة "
    "مع مراعاة tRmZ6 obligations"
)

# Consonant-cluster gibberish (no vowels) -- matches the pattern used in
# test_rfc020_f2f3_garble.py
_LATIN_GIBBERISH = " ".join(["xkjqz vbwm nfrl qpzx wblk"] * 60)


# ── 1. Bilingual Arabic+English must NOT be flagged ──────────────────────────


class TestBilingualArabicEnglishNotGarbled:
    """QF3 primary fix: bilingual domain English must not trip garble gate."""

    def test_is_garbled_blob_bilingual_not_flagged(self):
        assert check_garble(_BILINGUAL_ARABIC_ENGLISH, expected_script="Arab", profile=BULK_PROFILE) is False

    def test_flat_markdown_garble_bilingual_not_flagged(self):
        assert check_garble(_BILINGUAL_ARABIC_ENGLISH, expected_script="Arab", profile=FLAT_MARKDOWN_PROFILE) is False

    def test_tree_bulk_garble_bilingual_not_flagged(self):
        nodes = [{"text": _BILINGUAL_ARABIC_ENGLISH}]
        assert check_garble(_flatten_tree_text(nodes), expected_script="Arab", profile=BULK_PROFILE) is False

    def test_validate_tree_bilingual_passes(self):
        structure = [
            {
                "title": "SLA",
                "text": _BILINGUAL_ARABIC_ENGLISH,
                "nodes": [
                    {
                        "title": "scope",
                        "text": _BILINGUAL_ARABIC_ENGLISH,
                        "nodes": [
                            {"title": "detail", "text": _BILINGUAL_ARABIC_ENGLISH, "nodes": []},
                        ],
                    },
                    {"title": "terms", "text": _PURE_ARABIC, "nodes": []},
                ],
            }
        ]
        ok, reason = validate_tree(structure, expected_script="Arab")
        assert ok is True, f"Expected pass but got reason={reason}"

    def test_garble_check_nodes_english_node_in_bilingual_doc(self, caplog):
        """English-only node in a bilingual doc must NOT be flagged when
        _infer_script detects it as Latin, overriding filename-derived Arab."""
        nodes = [{"text": _PURE_ENGLISH, "nodes": []}]
        with caplog.at_level(logging.WARNING):
            count = _garble_check_nodes(nodes, page_script=None, expected_script="Arab")
        assert count == 0, "English-only node was falsely flagged as garbled"
        # Script mismatch should be logged (using text-inferred)
        assert any("text-inferred" in rec.message for rec in caplog.records)

    def test_pure_arabic_unchanged(self):
        """Pure Arabic text must still pass (no regression)."""
        assert check_garble(_PURE_ARABIC, expected_script="Arab", profile=BULK_PROFILE) is False

    def test_pure_english_no_expected_script(self):
        """English text without expected_script must still pass."""
        assert check_garble(_PURE_ENGLISH, expected_script=None, profile=BULK_PROFILE) is False

    def test_pure_english_with_latn_script(self):
        """English text with expected_script=Latn must pass."""
        assert check_garble(_PURE_ENGLISH, expected_script="Latn", profile=BULK_PROFILE) is False


# ── 2. Actually garbled text MUST still be detected ──────────────────────────


class TestActualGarbledStillDetected:
    """Safety: garble detection must NOT be weakened by QF3."""

    def test_null_bytes_detected(self):
        assert check_garble("some text\x00 with nulls", expected_script=None, profile=BULK_PROFILE) is True

    def test_replacement_char_detected(self):
        assert check_garble("some text � with replacement", expected_script=None, profile=BULK_PROFILE) is True

    def test_glyph_marker_detected(self):
        assert check_garble("text GLYPH<0042> more text", expected_script=None, profile=BULK_PROFILE) is True

    def test_pua_chars_detected(self):
        pua_text = "normal " + "" * 20 + " text"
        assert check_garble(pua_text, expected_script=None, profile=BULK_PROFILE) is True

    def test_consonant_cluster_gibberish_still_garbled(self):
        """Latin gibberish (no vowels) in Arabic context must still be caught."""
        assert check_garble(_LATIN_GIBBERISH, expected_script="Arab", profile=BULK_PROFILE) is True

    def test_digit_letter_mixed_garble_detected(self):
        """Digit-letter mixed tokens in Arabic context must be caught."""
        assert check_garble(_GARBLED_LATIN_IN_ARABIC, expected_script="Arab", profile=BULK_PROFILE) is True

    def test_empty_blob_detected(self):
        assert check_garble("", expected_script=None, profile=BULK_PROFILE) is True
        assert check_garble("   ", expected_script=None, profile=BULK_PROFILE) is True


# ── 3. Sparse mojibake still flagged ─────────────────────────────────────────


class TestSparseMojibakeRealCorruption:
    """_has_sparse_mojibake must still catch genuine encoding corruption."""

    def test_arabic_latin_arabic_glued_fragments(self):
        """Arabic-Latin(glued)-Arabic fragments above threshold are flagged."""
        # Build text with enough glued fragments to exceed 2% threshold
        clean = "كلمة " * 10
        fragment = "كلمةXYZكلمة "
        # 30 fragments out of ~70 tokens -> ~43% ratio, well above 2%
        text = clean + fragment * 30
        assert "sparse_mojibake" in garble_prongs(text, original_text=text)

    def test_clean_arabic_not_flagged(self):
        """Clean Arabic text must NOT trigger sparse mojibake."""
        assert "sparse_mojibake" not in garble_prongs(_PURE_ARABIC, original_text=_PURE_ARABIC)

    def test_short_text_exempt(self):
        """Text under 100 chars is exempt from sparse mojibake check."""
        short = "كلمةXYZكلمة " * 3
        assert len(short) < 100
        assert "sparse_mojibake" not in garble_prongs(short, original_text=short)


# ── 4. Morphological nonsense helper tests ───────────────────────────────────


class TestMorphologicalNonsense:
    """Direct tests for the _is_morphologically_nonsense helper."""

    def test_real_english_domain_words_not_nonsense(self):
        """Long domain words (>=5 chars) with vowels are plausible -> not nonsense."""
        real_words = [
            "service",
            "agreement",
            "availability",
            "infrastructure",
            "performance",
            "compliance",
            "escalation",
            "maintenance",
            "bandwidth",
            "latency",
            "provider",
            "customer",
            "contract",
            "termination",
            "liability",
            "Service",
            "Agreement",
        ]
        for word in real_words:
            assert _is_morphologically_nonsense(word) is False, f"{word} wrongly flagged"

    def test_common_short_words_not_nonsense(self):
        """Short words (3-4 chars) in _COMMON_WORDS are not nonsense."""
        common = ["the", "for", "and", "but", "not", "can", "our", "way", "use"]
        for word in common:
            assert _is_morphologically_nonsense(word) is False, f"{word} wrongly flagged"

    def test_garbled_tokens_are_nonsense(self):
        """Digit-letter mixed tokens are always nonsense."""
        garbled = ["xKjQ7", "mZpR3", "vBnL8", "wQxR5", "kLpZ9"]
        for token in garbled:
            assert _is_morphologically_nonsense(token) is True, f"{token} not flagged"

    def test_consonant_clusters_are_nonsense(self):
        """Vowel-less tokens (>=3 chars) are always nonsense."""
        consonant_only = ["xkjqz", "vbwm", "nfrl", "qpzx", "wblk"]
        for token in consonant_only:
            assert _is_morphologically_nonsense(token) is True, f"{token} not flagged"

    def test_tesseract_syllable_garble_caught(self):
        """Short Tesseract syllable garble (3-4 chars with vowels, not in
        _COMMON_WORDS) should be flagged as nonsense."""
        garble_syllables = [
            "Bab",
            "rel",
            "igh",
            "khar",
            "teb",
            "ghal",
            "mun",
            "sar",
            "dek",
            "phal",
            "wur",
            "foal",
            "pred",
        ]
        for token in garble_syllables:
            assert _is_morphologically_nonsense(token) is True, f"{token} not flagged"

    def test_short_tokens_exempt(self):
        assert _is_morphologically_nonsense("xy") is False

    def test_acronyms_exempt(self):
        acronyms = ["SLA", "PDF", "HTTP", "API", "HTML"]
        for acr in acronyms:
            assert _is_morphologically_nonsense(acr) is False, f"{acr} wrongly flagged"

    def test_long_acronym_not_exempt(self):
        # 6+ char all-caps without vowels should be flagged
        assert _is_morphologically_nonsense("XKJQZW") is True


# ── 5. Regression: existing garble detection cases ───────────────────────────


class TestQF3RegressionExistingGarbleCases:
    """Existing test scenarios from test_rfc020_f2f3_garble.py must still pass."""

    def test_tree_bulk_garble_latin_gibberish_with_arab_script(self):
        """Replicates TestExpectedScriptThreading via check_garble(TREE_BULK)."""
        nodes = [{"text": _LATIN_GIBBERISH}]
        assert check_garble(_flatten_tree_text(nodes), expected_script="Arab", profile=BULK_PROFILE) is True

    def test_tree_bulk_garble_real_arabic(self):
        """Replicates TestExpectedScriptThreading via check_garble(TREE_BULK)."""
        nodes = [{"text": _PURE_ARABIC}]
        assert check_garble(_flatten_tree_text(nodes), expected_script="Arab", profile=BULK_PROFILE) is False

    def test_flat_markdown_garble_latin_gibberish(self):
        """Replicates TestExpectedScriptThreading via check_garble(FLAT_MARKDOWN)."""
        assert check_garble(_LATIN_GIBBERISH, expected_script="Arab", profile=FLAT_MARKDOWN_PROFILE) is True

    def test_validate_tree_garble_fails(self):
        """Replicates TestExpectedScriptThreading.test_validate_tree_forwards_expected_script."""
        structure = [
            {
                "title": "root",
                "text": "root",
                "nodes": [
                    {
                        "title": "child",
                        "text": _LATIN_GIBBERISH,
                        "nodes": [{"title": "grandchild", "text": _LATIN_GIBBERISH, "nodes": []}],
                    },
                    {"title": "child2", "text": _LATIN_GIBBERISH, "nodes": []},
                ],
            }
        ]
        ok, reason = validate_tree(structure, expected_script="Arab")
        assert ok is False
        assert reason == "garbling"

    def test_garble_check_nodes_script_mismatch_logged(self, caplog):
        """Replicates TestExpectedScriptThreading.test_garble_check_nodes_expected_script_preference.
        NOTE: QF3 changed behavior -- inferred script now wins for per-node checks,
        but the mismatch is still logged."""
        latin_text = "The quick brown fox jumps over the lazy dog " * 5
        nodes = [{"text": latin_text, "nodes": []}]
        with caplog.at_level(logging.WARNING):
            count = _garble_check_nodes(nodes, page_script=None, expected_script="Arab")
        assert isinstance(count, int)
        assert any("mismatch" in rec.message.lower() for rec in caplog.records)

    def test_infer_script_latin(self):
        assert _infer_script(_PURE_ENGLISH) == "Latn"

    def test_infer_script_arabic(self):
        assert _infer_script(_PURE_ARABIC) == "Arab"
