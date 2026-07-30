"""RFC-020 Task 3.3 tests.

F2: expected_script threading through the garble-gate call chain
    (_script_from_filename -> _tree_is_garbled / _flat_text_is_garbled /
    validate_tree / _garble_check_nodes).
F3: OCR lang override -- detect_ocr_langs() driving the docling
    ocr_lang_override re-run in client.py's pre-garble probe.
"""

import logging

from pageindex_mcp.converters import detect_ocr_langs
from pageindex_mcp.helpers import (
    _flat_text_is_garbled,
    _garble_check_nodes,
    _infer_script,
    _script_from_filename,
    _tree_is_garbled,
    validate_tree,
)

# A blob of Latin-alphabet consonant clusters -- no real words in any language,
# long enough to clear the >20-token repetition-check floor and the Latin-
# gibberish ratio threshold used by _is_garbled_blob(expected_script="Arab").
_LATIN_GIBBERISH = " ".join(["xkjqz vbwm nfrl qpzx wblk"] * 60)

_REAL_ARABIC = "بسم الله الرحمن الرحيم " * 20


class TestExpectedScriptThreading:
    def test_script_from_filename_arabic(self):
        assert _script_from_filename("وارد_597.pdf") == "Arab"

    def test_script_from_filename_german(self):
        assert _script_from_filename("Haftpflicht_2024.pdf") is None

    def test_tree_is_garbled_with_arab_script_latin_gibberish(self):
        nodes = [{"text": _LATIN_GIBBERISH}]
        assert _tree_is_garbled(nodes, expected_script="Arab") is True

    def test_tree_is_garbled_with_none_script_latin_gibberish(self):
        nodes = [{"text": _LATIN_GIBBERISH}]
        # No expected_script -> the Latin-gibberish-in-non-Latin-context check
        # never engages; whatever the bulk heuristics decide, it must not crash.
        result = _tree_is_garbled(nodes, expected_script=None)
        assert isinstance(result, bool)

    def test_tree_is_garbled_real_arabic_text(self):
        nodes = [{"text": _REAL_ARABIC}]
        assert _tree_is_garbled(nodes, expected_script="Arab") is False

    def test_flat_text_is_garbled_with_arab_script(self):
        assert _flat_text_is_garbled(_LATIN_GIBBERISH, expected_script="Arab") is True

    def test_garble_check_nodes_expected_script_preference(self, caplog):
        # Node text is Latin-script-inferred, but the caller passes an Arabic
        # expected_script derived from the filename -- expected_script must win
        # and the mismatch must be logged.
        latin_text = "The quick brown fox jumps over the lazy dog " * 5
        nodes = [{"text": latin_text, "nodes": []}]
        with caplog.at_level(logging.WARNING):
            count = _garble_check_nodes(nodes, page_script=None, expected_script="Arab")
        assert isinstance(count, int)
        assert any("mismatch" in rec.message.lower() for rec in caplog.records)

    def test_garble_check_nodes_fallback_to_infer(self):
        # Without an expected_script, the function must fall back to
        # _infer_script() per-node rather than raising or ignoring text.
        latin_text = "The quick brown fox jumps over the lazy dog " * 5
        nodes = [{"text": latin_text, "nodes": []}]
        assert _infer_script(latin_text) in ("Latn", None)
        count = _garble_check_nodes(nodes, page_script=None, expected_script=None)
        assert isinstance(count, int)

    def test_validate_tree_forwards_expected_script(self):
        # End-to-end: validate_tree(expected_script="Arab") over a tall-enough
        # tree of Latin gibberish must fail with "garbling", not silently pass.
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


class TestOcrLangOverride:
    def test_detect_ocr_langs_arabic_filename(self):
        langs = detect_ocr_langs("وارد_597.pdf")
        assert "ara" in langs

    def test_detect_ocr_langs_german_filename(self):
        langs = detect_ocr_langs("Straßenverkehrsversicherung.pdf")
        assert "ara" not in langs
        assert "deu" in langs
