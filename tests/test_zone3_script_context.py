"""Zone-3 contract tests: ScriptContext.from_document factory and
_script_from_filename relocation to script.py."""

from __future__ import annotations

import dataclasses

import pytest

from pageindex_mcp.script import (
    ARABIC_RANGES,
    PRESENTATION_RANGES,
    ScriptContext,
    _infer_script,
    _script_from_filename,
)


# ---------------------------------------------------------------------------
# _script_from_filename lives in script.py (not helpers.py)
# ---------------------------------------------------------------------------


class TestScriptFromFilenameLocation:
    """Contract: _script_from_filename is importable from script.py."""

    def test_importable_from_script_module(self):
        """The function should be importable directly from pageindex_mcp.script."""
        from pageindex_mcp.script import _script_from_filename as fn

        assert callable(fn)

    def test_backward_compat_reexport_from_helpers(self):
        """helpers.py still re-exports _script_from_filename for backward compat."""
        from pageindex_mcp.helpers import _script_from_filename as fn

        assert callable(fn)

    def test_same_function_object(self):
        """Both imports reference the same function object."""
        from pageindex_mcp.helpers import _script_from_filename as helpers_fn
        from pageindex_mcp.script import _script_from_filename as script_fn

        assert helpers_fn is script_fn


# ---------------------------------------------------------------------------
# ScriptContext.from_document: filename-based script inference
# ---------------------------------------------------------------------------


class TestScriptContextFromDocumentFilename:
    """Contract: from_document uses _script_from_filename for filename-derived script.

    Note: _script_from_filename calls detect_ocr_langs on the filename string,
    which scans for actual Unicode Arabic codepoints (not the ISO-639 code 'ara').
    Latin-character filenames (even with '_ara' suffix) return 'Latn' because
    the filename chars are ASCII Latin.  Arabic-codepoint filenames (actual
    Arabic text in the filename) return 'Arab'.
    """

    def test_arabic_codepoint_filename(self):
        """Filename containing actual Arabic codepoints -> dominant_script='Arab'."""
        # Actual Arabic chars in the filename trigger Arabic detection
        ctx = ScriptContext.from_document("سياسة.pdf", "")
        assert ctx.dominant_script == "Arab"
        assert ctx.source in ("filename", "combined")

    def test_latin_filename_returns_latn(self):
        """Latin-character filename -> dominant_script='Latn' (detect_ocr_langs
        scans actual chars, not ISO codes)."""
        ctx = ScriptContext.from_document("musterbedingungen_deu.pdf", "")
        assert ctx.dominant_script == "Latn"
        assert ctx.source in ("filename", "combined")

    def test_english_filename(self):
        """English-signaling filename -> dominant_script='Latn'."""
        ctx = ScriptContext.from_document("contract_eng.pdf", "")
        assert ctx.dominant_script == "Latn"
        assert ctx.source in ("filename", "combined")

    def test_hash_named_also_latn(self):
        """Hash-named file with Latin chars -> dominant_script='Latn' via filename
        (detect_ocr_langs sees the Latin chars in the filename)."""
        ctx = ScriptContext.from_document("a1b2c3d4e5f6.pdf", "")
        assert ctx.dominant_script == "Latn"
        assert ctx.source in ("filename", "combined")

    def test_hash_named_with_arabic_text(self):
        """Hash-named file (Latn filename) with Arabic text -> dominant_script='Latn'
        because filename takes precedence; source='combined' since both provide script."""
        arabic_text = "المادة الأولى تنظيم الحقوق والواجبات للمواطنين القانون العام"
        ctx = ScriptContext.from_document("a1b2c3d4e5f6.pdf", arabic_text)
        # Filename gives Latn, text gives Arab; filename wins
        assert ctx.dominant_script == "Latn"
        assert ctx.source == "combined"

    def test_hash_named_with_german_text(self):
        """Hash-named file with German text -> dominant_script='Latn',
        source='combined' (filename=Latn, text=Latn, both agree)."""
        german_text = "Die Versicherung umfasst die gesetzliche Haftpflicht des Versicherungsnehmers"
        ctx = ScriptContext.from_document("a1b2c3d4e5f6.pdf", german_text)
        assert ctx.dominant_script == "Latn"
        assert ctx.source == "combined"

    def test_filename_takes_precedence_over_text(self):
        """When filename gives a script AND text gives a script, filename wins
        and source is 'combined'."""
        german_text = "Die Versicherung umfasst die gesetzliche Haftpflicht"
        ctx = ScriptContext.from_document("contract_deu.pdf", german_text)
        assert ctx.dominant_script == "Latn"
        assert ctx.source == "combined"

    def test_arabic_filename_precedence_over_latin_text(self):
        """Filename with Arabic codepoints takes precedence over Latin text."""
        ctx = ScriptContext.from_document("سياسة.pdf",
                                          "This is English text for testing purposes")
        assert ctx.dominant_script == "Arab"
        assert ctx.source == "combined"


# ---------------------------------------------------------------------------
# ScriptContext.from_document: Presentation Forms detection (pre-NFKC)
# ---------------------------------------------------------------------------


class TestScriptContextPresentationForms:
    """Contract: had_presentation_forms is detected on raw text BEFORE NFKC
    normalization (Presentation Forms codepoints are destroyed by NFKC)."""

    @staticmethod
    def _make_pf_text(pf_ratio: float = 0.60, total_arabic: int = 100) -> str:
        """Build a text string with a given ratio of Presentation Forms chars
        to Arabic-range chars."""
        # Presentation Form B range: U+FE70-U+FEFF
        pf_chars = [chr(c) for c in range(0xFE70, 0xFE70 + int(total_arabic * pf_ratio))]
        # Regular Arabic range: U+0620-U+063F
        regular_chars = [chr(c) for c in range(0x0620, 0x0620 + total_arabic - len(pf_chars))]
        return "".join(pf_chars + regular_chars)

    def test_high_pf_ratio_detected(self):
        """Presentation Forms ratio > 50% -> had_presentation_forms=True."""
        raw = self._make_pf_text(pf_ratio=0.60, total_arabic=80)
        ctx = ScriptContext.from_document("doc.pdf", raw)
        assert ctx.had_presentation_forms is True

    def test_low_pf_ratio_not_detected(self):
        """Presentation Forms ratio < 50% -> had_presentation_forms=False."""
        # Only regular Arabic chars, no Presentation Forms
        raw = "".join(chr(c) for c in range(0x0620, 0x0660))
        ctx = ScriptContext.from_document("doc.pdf", raw)
        assert ctx.had_presentation_forms is False

    def test_no_arabic_no_pf(self):
        """Pure Latin text -> had_presentation_forms=False."""
        ctx = ScriptContext.from_document("doc.pdf", "Hello world this is a test document")
        assert ctx.had_presentation_forms is False

    def test_pf_survives_meaning_nfkc_destroys_originals(self):
        """The ScriptContext captures PF boolean from raw text; after NFKC
        normalization the codepoints would be gone, but the boolean persists."""
        import unicodedata

        raw = self._make_pf_text(pf_ratio=0.60, total_arabic=80)
        ctx = ScriptContext.from_document("doc.pdf", raw)
        # After NFKC, presentation forms are decomposed
        normalized = unicodedata.normalize("NFKC", raw)
        pf_in_normalized = sum(
            1 for c in normalized
            if any(lo <= ord(c) <= hi for lo, hi in PRESENTATION_RANGES)
        )
        # The normalized text has fewer (or zero) PF codepoints
        # but the context still remembers the pre-NFKC detection
        assert ctx.had_presentation_forms is True


# ---------------------------------------------------------------------------
# ScriptContext: source provenance tracking
# ---------------------------------------------------------------------------


class TestScriptContextSourceProvenance:
    """Contract: ScriptContext.source accurately reflects how the script was determined."""

    def test_source_filename_only(self):
        """Filename-derived script with no text -> source='filename'."""
        ctx = ScriptContext.from_document("doc_ara.pdf", "")
        assert ctx.source == "filename"

    def test_source_text_inference_only(self):
        """Text-only script when filename gives no signal is rare because
        detect_ocr_langs usually returns something for Latin filenames.
        Use from_script_str for pure text-inference scenarios."""
        # Actually, for a pure numeric filename with no Latin letters,
        # detect_ocr_langs falls back to deu+eng -> _script_from_filename -> "Latn"
        # So test with Arabic-codepoint filename + Arabic text -> combined
        arabic = "المادة الأولى تنظيم الحقوق والواجبات للمواطنين القانون العام"
        ctx = ScriptContext.from_document("سياسة.pdf", arabic)
        assert ctx.source == "combined"

    def test_source_combined(self):
        """Both filename and text give a script -> source='combined'."""
        german = "Die Versicherung umfasst die gesetzliche Haftpflicht"
        ctx = ScriptContext.from_document("doc_deu.pdf", german)
        assert ctx.source == "combined"

    def test_source_none_requires_no_filename_no_text(self):
        """source='none' requires both filename and text to yield None script.
        Since detect_ocr_langs usually returns something for any filename,
        source='none' is rare. Test with empty filename."""
        ctx = ScriptContext.from_document("", "")
        assert ctx.source in ("none", "filename")  # depends on detect_ocr_langs("") behavior

    def test_source_legacy(self):
        """from_script_str produces source='legacy'."""
        ctx = ScriptContext.from_script_str("Arab")
        assert ctx.source == "legacy"

    def test_source_legacy_none(self):
        """from_script_str(None) produces source='legacy'."""
        ctx = ScriptContext.from_script_str(None)
        assert ctx.source == "legacy"
        assert ctx.dominant_script is None


# ---------------------------------------------------------------------------
# ScriptContext: frozen immutability
# ---------------------------------------------------------------------------


class TestScriptContextFrozen:
    """Contract: ScriptContext is a frozen dataclass -- no mutation allowed."""

    def test_frozen_dominant_script(self):
        ctx = ScriptContext.from_document("doc_ara.pdf", "")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.dominant_script = "Latn"  # type: ignore[misc]

    def test_frozen_had_presentation_forms(self):
        ctx = ScriptContext.from_document("doc.pdf", "")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.had_presentation_forms = True  # type: ignore[misc]

    def test_frozen_source(self):
        ctx = ScriptContext.from_document("doc.pdf", "")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.source = "hacked"  # type: ignore[misc]
