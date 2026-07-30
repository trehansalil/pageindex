"""Tests for D2 garble gate: Latin-gibberish detection in non-Latin contexts (RFC-019)."""

import os
from unittest.mock import patch

from pageindex_mcp.helpers import (
    _garble_check_nodes,
    _infer_script,
    _is_garbled_blob,
    validate_tree,
)


class TestInferScript:
    """_infer_script detects dominant Unicode script."""

    def test_arabic_text(self):
        text = "هذا نص عربي طويل بما فيه الكفاية للكشف عن النص"
        assert _infer_script(text) == "Arab"

    def test_latin_text(self):
        text = "This is a sufficiently long English text for detection"
        assert _infer_script(text) == "Latn"

    def test_german_text(self):
        text = "Dies ist ein ausreichend langer deutscher Text zur Erkennung"
        assert _infer_script(text) == "Latn"

    def test_short_text_returns_none(self):
        assert _infer_script("hi") is None
        assert _infer_script("مرحبا") is None

    def test_empty_returns_none(self):
        assert _infer_script("") is None
        assert _infer_script("   ") is None

    def test_mixed_ambiguous(self):
        text = "12345 67890 !@#$% ^&*()"
        assert _infer_script(text) is None


class TestLatinGibberishDetection:
    """_is_garbled_blob Latin-gibberish prong (D2)."""

    def test_arabic_tesseract_garble_detected(self):
        """Simulated Tesseract ara mis-recognition: Latin nonsense in Arabic context."""
        garbled = "de Bab rel igh foal pred khar teb ghal mun sar dek phal wur"
        assert _is_garbled_blob(garbled, expected_script="Arab") is True

    def test_clean_arabic_not_flagged(self):
        """Clean Arabic text must not be flagged."""
        arabic = "هذا نص عربي نظيف يجب أن يمر بنجاح"
        assert _is_garbled_blob(arabic, expected_script="Arab") is False

    def test_legitimate_bilingual_not_flagged(self):
        """Arabic text with legitimate English words (company names, technical terms)."""
        bilingual = (
            "شركة Google و Microsoft تعملان في مجال التكنولوجيا وتقدمان خدمات the cloud computing"
        )
        assert _is_garbled_blob(bilingual, expected_script="Arab") is False

    def test_latin_context_ignores_prong(self):
        """Latin script context -> Latin-gibberish prong should NOT fire."""
        nonsense = "xkq plm zfg wrt bvn yhs tjk mld qrx"
        assert _is_garbled_blob(nonsense, expected_script="Latn") is False

    def test_no_expected_script_ignores_prong(self):
        """No expected_script -> the Latin-gibberish prong specifically must not fire.

        Without expected_script, other bulk heuristics could in principle still
        flag the blob, but for this token count/length they do not, so the net
        result is False -- confirming the D2 prong is what depends on
        expected_script being set.
        """
        garbled = "de Bab rel igh foal pred khar teb ghal mun sar dek phal wur"
        assert _is_garbled_blob(garbled, expected_script=None) is False

    def test_few_latin_tokens_below_threshold(self):
        """Fewer than 5 Latin tokens -> prong doesn't fire even in Arabic context."""
        short = "abc def ghi"
        assert _is_garbled_blob(short, expected_script="Arab") is False

    def test_env_disable(self):
        """GARBLE_LATIN_GIBBERISH_ENABLED=false disables the prong."""
        garbled = "de Bab rel igh foal pred khar teb ghal mun sar dek phal wur"
        with patch.dict(os.environ, {"GARBLE_LATIN_GIBBERISH_ENABLED": "false"}):
            assert _is_garbled_blob(garbled, expected_script="Arab") is False

    def test_env_ratio_override(self):
        """GARBLE_LATIN_RATIO override changes threshold."""
        # Text with ~75% Latin tokens (6/8) -- above default 0.4 but below custom 0.8.
        mixed = "de Bab rel igh foal pred هذا نص عربي"
        with patch.dict(os.environ, {"GARBLE_LATIN_RATIO": "0.8"}):
            assert _is_garbled_blob(mixed, expected_script="Arab") is False


class TestGarbleCheckNodesWithScript:
    """_garble_check_nodes threads expected_script correctly.

    NOTE: _garble_check_nodes infers a per-node script from node text >= 50
    chars (falling back to page_script only for shorter nodes), so garbled
    node text used here mixes Latin gibberish into a majority-Arabic blob
    (by character count) so the per-node inference still resolves to "Arab"
    and the D2 prong can fire.
    """

    GARBLED_NODE_TEXT = (
        "المسؤوليةالقانونيةوالالتزاماتالتعاقدية de Bab rel igh foal pred "
        "الشروطوالأحكامالمرفقةبالعقد"
    )

    def test_arabic_garble_caught_with_page_script(self):
        """Nodes with Latin gibberish flagged when page_script='Arab'."""
        nodes = [
            {"text": self.GARBLED_NODE_TEXT, "nodes": []},
            {"text": "هذا نص عربي نظيف يجب أن يمر بنجاح", "nodes": []},
        ]
        garbled_count = _garble_check_nodes(nodes, page_script="Arab")
        assert garbled_count >= 1  # at least the garbled node

    def test_clean_nodes_zero_garbled(self):
        """Clean Latin nodes with no page_script -> zero garbled."""
        nodes = [
            {
                "text": "This is perfectly clean English text that should pass all checks",
                "nodes": [],
            },
            {"text": "Another clean node with normal content here too", "nodes": []},
        ]
        assert _garble_check_nodes(nodes) == 0

    def test_nested_garble_counted(self):
        """Garbled child node in nested structure is counted."""
        nodes = [
            {
                "text": "Clean parent node text",
                "nodes": [
                    {"text": self.GARBLED_NODE_TEXT, "nodes": []},
                ],
            },
        ]
        garbled_count = _garble_check_nodes(nodes, page_script="Arab")
        assert garbled_count >= 1


class TestValidateTreeWithD2:
    """validate_tree integrates D2 garble detection end-to-end."""

    def test_tree_with_majority_garbled_arabic_nodes_fails(self):
        """Tree where most nodes have Latin gibberish in an Arabic doc -> fails validation."""
        garbled_text = (
            "المسؤوليةالقانونيةوالالتزاماتالتعاقدية de Bab rel igh foal pred "
            "الشروطوالأحكامالمرفقةبالعقد"
        )
        structure = [
            {
                "node_id": "1",
                "title": "Root",
                "text": "عنوان الوثيقة الرسمية الكاملة",
                "nodes": [
                    {
                        "node_id": "2",
                        "title": "S1",
                        "text": garbled_text,
                        "nodes": [
                            {"node_id": "3", "title": "S1.1", "text": garbled_text, "nodes": []},
                        ],
                    },
                    {"node_id": "4", "title": "S2", "text": garbled_text, "nodes": []},
                ],
            },
        ]
        ok, reason = validate_tree(structure)
        # Most nodes carry Latin-gibberish-in-Arabic-context content, so the
        # tree must fail. It may be caught either by the D2 per-node ratio
        # gate ("node_garbling") or by the bulk garble heuristics running
        # earlier in validate_tree ("garbling") -- both are valid outcomes
        # of the same underlying garble-gate machinery.
        assert not ok
        assert reason in ("node_garbling", "garbling")
