# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Script classification and text-layer language pre-classification.

Consolidates:
  - test_zone5_script.py            (core script.py + _garble_prongs + order_verdict)
  - test_zone1_script_from_filename.py  (_script_from_filename regression)
  - test_zone5_script_drift.py      (CI guard: no hardcoded Arabic ranges outside script.py)
  - test_preclassify.py             (converters.preclassify, RFC-046 D4)

Table-driven tests loop internally and collect every failing row so a single
collected test still names each offending case.
"""

from __future__ import annotations

import pytest

from pageindex_mcp.converters.preclassify import (
    PreClassification,
    _classify_langs_from_filename,
    _iso_to_tess,
    detect_lang_from_text_layer,
    merge_lang_sources,
    preclassify_document,
)
from pageindex_mcp.helpers import _script_from_filename
from pageindex_mcp.script import (
    _AR_COMMON_WORDS,
    AR_CHAR_RE,
    ScriptContext,
    arabic_char_count,
    arabic_ratio,
    arabic_readability_score,
    is_arabic_char,
)


# ---------------------------------------------------------------------------
# 1. Arabic codepoint primitives -- is_arabic_char / arabic_ratio /
#    arabic_char_count / AR_CHAR_RE / arabic_readability_score
# ---------------------------------------------------------------------------


class TestArabicCodepointPrimitives:
    def test_codepoint_classification_table(self):
        """Every Arabic block is recognised; Latin is not.

        Table-driven: collects every mismatching row rather than failing on
        the first, so one collected test names all offenders.
        """
        rows = [
            # (char, expected_is_arabic, label)
            ("ع", True, "base_range U+0600-06FF"),
            ("ݐ", True, "supplement_start U+0750-077F"),
            ("ﭐ", True, "presentation_a_start U+FB50-FDFF"),
            ("ﹰ", True, "presentation_b_start U+FE70-FEFF"),
            ("A", False, "latin_upper"),
            ("z", False, "latin_lower"),
            ("7", False, "digit"),
            (" ", False, "space"),
        ]
        failures = [
            f"{label}: is_arabic_char({char!r}) == {is_arabic_char(char)!r}, expected {want!r}"
            for char, want, label in rows
            if is_arabic_char(char) is not want
        ]
        assert not failures, "is_arabic_char misclassified:\n" + "\n".join(failures)

        # AR_CHAR_RE must agree with is_arabic_char on the same table.
        regex_failures = [
            f"{label}: AR_CHAR_RE.search({char!r}) truthiness != {want!r}"
            for char, want, label in rows
            if bool(AR_CHAR_RE.search(char)) is not want
        ]
        assert not regex_failures, "AR_CHAR_RE disagrees:\n" + "\n".join(regex_failures)
        assert AR_CHAR_RE.search("Hello World") is None

    def test_ratio_and_count(self):
        assert arabic_ratio("عربي") == 1.0
        assert arabic_ratio("abcd") == 0.0
        assert arabic_char_count("abcعربxyz") == 3
        assert arabic_char_count("abcxyz") == 0

    def test_readability_common_words(self):
        words = list(_AR_COMMON_WORDS)[:3]
        assert arabic_readability_score(words) > 0
        assert arabic_readability_score(["zzz", "qqq", "xxx"]) == 0


# ---------------------------------------------------------------------------
# 2. _script_from_filename  (zone-1 regression)
#
# Regression target: a German-style filename (Haftpflicht) must return 'Latn',
# not None -- the pre-fix behavior silently disabled the latin_gibberish prong.
# ---------------------------------------------------------------------------


class TestScriptFromFilename:
    def test_script_from_filename_table(self):
        """LANG-01-C1 (script side): filename language signals resolve to a
        Unicode script tag -- 'Arab' for Arabic-signalling names, 'Latn' for
        deu/eng names.  Never None for a name carrying any letter signal."""
        rows = [
            ("وارد_597.pdf", "Arab", "arabic_warid"),
            ("تأمين_شامل.pdf", "Arab", "arabic_insurance"),
            ("Versicherungsbedingungen_AHB.pdf", "Latn", "german_compound"),
            ("General_Conditions.pdf", "Latn", "english_general_conditions"),
            ("Haftpflicht_2024.pdf", "Latn", "german_haftpflicht_regression"),
        ]
        failures = [
            f"{label}: _script_from_filename({name!r}) == {_script_from_filename(name)!r}, "
            f"expected {want!r}"
            for name, want, label in rows
            if _script_from_filename(name) != want
        ]
        assert not failures, "\n".join(failures)
        # A letterless/hash filename must degrade, never crash.
        assert _script_from_filename("8f3a91c2.pdf") in ("Latn", None)


# ---------------------------------------------------------------------------
# 3. order_verdict  (RTL reading-order arbitration)
# ---------------------------------------------------------------------------


class TestOrderVerdict:
    def test_empty_input(self):
        from pageindex_mcp.script import order_verdict

        v = order_verdict("")
        assert v.reversed is False
        assert v.sampled == 0

    def test_vocab_list_method_detects_reversed(self):
        from pageindex_mcp.script import order_verdict

        known = ("مادة", "باب")
        known_rev = tuple(w[::-1] for w in known)
        text = "ةدام some reversed text here"
        v = order_verdict(
            [text],
            method="vocab_list",
            aggregate=False,
            fail_threshold=0.0,
            known_words=known,
            known_words_reversed=known_rev,
        )
        assert v.reversed is True

    def test_require_orig_positive_prevents_false_positive(self):
        from pageindex_mcp.script import order_verdict

        text = "xxxxxxxxxx yyyyyyyyyy zzzzzzzzzz"
        v = order_verdict(
            text,
            unit="single",
            method="readability_display",
            aggregate=True,
            require_orig_positive=True,
        )
        assert v.reversed is False

    def test_readability_word_reverse_method(self):
        from pageindex_mcp.script import order_verdict

        correct_arabic = "في هذا النص العربي الطويل"
        reversed_words = " ".join(reversed(correct_arabic.split()))
        v = order_verdict(
            reversed_words,
            unit="single",
            method="readability_word_reverse",
            aggregate=True,
        )
        assert v.sampled == 1


# ---------------------------------------------------------------------------
# 4. ScriptContext.from_script_str deprecation + required PF param (RFC-043 D3)
# ---------------------------------------------------------------------------


class TestFromScriptStrDeprecation:
    def test_warns_and_passes_through_supplied_value(self):
        with pytest.deprecated_call():
            ctx = ScriptContext.from_script_str("Arab", had_presentation_forms=True)
        assert ctx.dominant_script == "Arab"
        assert ctx.had_presentation_forms is True
        assert ctx.source == "legacy"

    def test_had_presentation_forms_is_required_and_keyword_only(self):
        with pytest.raises(TypeError):
            ScriptContext.from_script_str("Arab")
        with pytest.raises(TypeError):
            ScriptContext.from_script_str("Arab", True)


# ---------------------------------------------------------------------------
# 5. Garble-prong smoke coverage reachable from the script surface
#    (the full prong matrix lives in tests/test_garble.py)
# ---------------------------------------------------------------------------


class TestGarbleProngsFromScriptSurface:
    def test_prong_table(self):
        from pageindex_mcp.helpers.garble import _garble_prongs

        long_digits = "1234567890" * 60
        clean = (
            "This is a perfectly normal English paragraph with no garbling issues whatsoever. " * 3
        )
        rows = [
            ("", "empty", True, "empty_string"),
            ("   ", "empty", True, "whitespace_only"),
            ("text with GLYPH< markers present", "glyph_marker", True, "glyph_marker"),
            (long_digits, "digit_ratio", True, "digit_ratio_above_floor"),
            (clean, "digit_ratio", False, "clean_prose_no_digit_ratio"),
            (clean, "glyph_marker", False, "clean_prose_no_glyph_marker"),
        ]
        failures = []
        for text, prong, want, label in rows:
            got = prong in _garble_prongs(text)
            if got is not want:
                failures.append(f"{label}: {prong!r} present={got}, expected {want}")
        assert not failures, "\n".join(failures)
        assert _garble_prongs(clean) == frozenset(), "clean prose must fire no prong at all"


# ---------------------------------------------------------------------------
# 6. Backward-compatible import paths
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_legacy_import_paths_still_resolve(self):
        from pageindex_mcp.converters import _is_arabic_char
        from pageindex_mcp.helpers import normalize_dashes
        from pageindex_mcp.script import _JOINING_TYPE

        assert _is_arabic_char("ع") is True
        assert _is_arabic_char("A") is False
        assert normalize_dashes("−") == "-"
        assert _JOINING_TYPE[ord("ب")] == "D"


# ===========================================================================
# --- merged from tests/test_preclassify.py (RFC-046 D4) ---
# ===========================================================================


class TestClassifyLangsFromFilename:
    def test_filename_language_table(self):
        """_classify_langs_from_filename maps filename signals to ISO codes."""
        rows = [
            # (filename, must_contain, must_equal_or_None, label)
            ("سياسة حوكمة.pdf", {"ar"}, None, "arabic"),
            ("Haftpflicht-Bedingungen.pdf", {"de"}, None, "german_keyword"),
            ("document.pdf", set(), ["en"], "english_fallback"),
            ("", set(), ["en"], "empty_filename"),
            ("MOU MOHRE & وزارة الصناعة.pdf", {"ar", "en"}, None, "mixed_arabic_latin"),
        ]
        failures = []
        for name, must_contain, exact, label in rows:
            got = _classify_langs_from_filename(name)
            if exact is not None and got != exact:
                failures.append(f"{label}: got {got!r}, expected exactly {exact!r}")
            missing = must_contain - set(got)
            if missing:
                failures.append(f"{label}: got {got!r}, missing {sorted(missing)}")
        assert not failures, "\n".join(failures)


class TestIsoToTess:
    def test_iso_to_tess_table(self):
        rows = [
            ([], [], "empty"),
            (["fr"], ["fra"], "single"),
            (["ar", "de", "en"], ["ara", "deu", "eng"], "standard_mappings"),
            (["zh", "en"], ["zh", "eng"], "unknown_passthrough"),
        ]
        failures = [
            f"{label}: _iso_to_tess({src!r}) == {_iso_to_tess(src)!r}, expected {want!r}"
            for src, want, label in rows
            if _iso_to_tess(src) != want
        ]
        assert not failures, "\n".join(failures)


class TestDetectLangFromTextLayer:
    def test_non_text_layer_paths_degrade(self, tmp_path):
        """A non-PDF and a missing file both degrade without raising."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8")
        assert detect_lang_from_text_layer(str(img)).lang_source != "text_layer"
        assert detect_lang_from_text_layer("/nonexistent/file.pdf").lang_source == "error"


class TestMergeLangSources:
    def test_filename_only_when_no_text_layer(self):
        result = merge_lang_sources("Haftpflicht.pdf", None)
        assert result.lang_source == "filename"
        assert "de" in result.detected_langs

    def test_text_layer_reclassifies_and_deduplicates(self):
        text_layer = PreClassification(
            detected_langs=["ar", "en"],
            lang_source="text_layer",
            text_layer_chars=5000,
            arabic_ratio=0.7,
        )
        # Filename says English; the text layer disagrees and wins.
        result = merge_lang_sources("document.pdf", text_layer)
        assert "ar" in result.detected_langs
        assert result.lang_source == "text_layer"
        # Filename and text layer agree on "ar" -- the merge must not duplicate.
        merged = merge_lang_sources("سياسة.pdf", text_layer)
        assert merged.detected_langs.count("ar") == 1
        assert merged.detected_langs.count("en") == 1

    def test_text_layer_confirms(self):
        text_layer = PreClassification(
            detected_langs=["en"],
            lang_source="text_layer",
            text_layer_chars=3000,
        )
        result = merge_lang_sources("report.pdf", text_layer)
        assert result.lang_source == "text_layer_confirmed"

    def test_garbled_text_layer_falls_back(self):
        garbled = PreClassification(
            lang_source="garbled_text_layer",
            garbled_text_layer=True,
        )
        result = merge_lang_sources("سياسة.pdf", garbled)
        assert result.lang_source == "filename"
        assert "ar" in result.detected_langs


class TestPreclassifyDocument:
    def test_non_pdf_routing_table(self):
        """Non-PDF inputs classify by extension and filename language."""
        rows = [
            # (path, filename, file_type, ocr_langs_contains, label)
            ("/tmp/test.jpg", "chart.jpg", "image", {"eng"}, "image_english"),
            ("/tmp/test.png", "سياسة.png", "image", {"ara"}, "image_arabic"),
            ("/tmp/test.docx", "report.docx", "other", {"eng"}, "other_docx"),
        ]
        failures = []
        for path, name, want_type, want_langs, label in rows:
            result = preclassify_document(path, name, run_inspector=False)
            if result.file_type != want_type:
                failures.append(f"{label}: file_type {result.file_type!r} != {want_type!r}")
            if not want_langs <= set(result.ocr_langs):
                failures.append(f"{label}: ocr_langs {result.ocr_langs!r} missing {want_langs}")
            if result.lang_source != "filename":
                failures.append(f"{label}: lang_source {result.lang_source!r} != 'filename'")
            if result.pdf_type is not None:
                failures.append(f"{label}: pdf_type {result.pdf_type!r} should be None")
        assert not failures, "\n".join(failures)

    def test_ocr_langs_are_tess_codes_and_timing_recorded(self):
        result = preclassify_document("/tmp/test.jpg", "Haftpflicht.jpg", run_inspector=False)
        assert result.ocr_langs, "ocr_langs must never be empty"
        for lang in result.ocr_langs:
            assert lang not in ("ar", "de", "en"), f"ISO code leaked: {lang}"
        assert result.elapsed_ms >= 0


class TestPreClassificationSerialisation:
    def test_roundtrip_with_all_fields(self):
        pc = PreClassification(
            file_type="pdf",
            pdf_type="scanned",
            pdf_confidence=0.95,
            pages_needing_ocr=[0, 1, 2],
            has_encoding_issues=True,
            page_count=42,
            detected_langs=["ar", "en"],
            lang_source="text_layer",
            text_sample="sample",
            text_layer_chars=5000,
            arabic_ratio=0.65,
            has_german_markers=False,
            garbled_text_layer=False,
            ocr_langs=["ara", "eng"],
            elapsed_ms=12.5,
        )
        restored = PreClassification.from_dict(pc.to_dict())
        assert restored.file_type == "pdf"
        assert restored.pdf_type == "scanned"
        assert restored.pdf_confidence == pytest.approx(0.95, abs=0.001)
        assert restored.pages_needing_ocr == [0, 1, 2]
        assert restored.has_encoding_issues is True
        assert restored.page_count == 42
        assert restored.ocr_langs == ["ara", "eng"]
        assert restored.detected_langs == ["ar", "en"]
        assert restored.lang_source == "text_layer"
        assert restored.arabic_ratio == pytest.approx(0.65, abs=0.001)

    def test_roundtrip_garbled_fields(self):
        pc = PreClassification(
            garbled_text_layer=True,
            alpha_ratio=0.05,
            junk_ratio=0.60,
        )
        d = pc.to_dict()
        assert d["garbled_text_layer"] is True
        restored = PreClassification.from_dict(d)
        assert restored.garbled_text_layer is True
        assert restored.alpha_ratio == pytest.approx(0.05, abs=0.001)
        assert restored.junk_ratio == pytest.approx(0.60, abs=0.001)

    def test_from_dict_backward_compat(self):
        """Old-format dicts (missing new fields) deserialise with safe defaults."""
        old_dict = {
            "detected_langs": ["en"],
            "lang_source": "filename",
            "text_layer_chars": 0,
            "elapsed_ms": 1.0,
        }
        pc = PreClassification.from_dict(old_dict)
        assert pc.file_type == "pdf"
        assert pc.pdf_type is None
        assert pc.ocr_langs == ["eng"]
        assert pc.page_count == 0
        assert pc.garbled_text_layer is False

    def test_pdf_type_none_omits_inspector_fields(self):
        """When pdf_type is None, inspector fields are absent from the dict."""
        d = PreClassification(pdf_type=None).to_dict()
        assert "pdf_type" not in d
        assert "pdf_confidence" not in d
        assert "pages_needing_ocr" not in d
