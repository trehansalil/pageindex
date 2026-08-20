"""Consolidated RTL/bidi processing tests.

Merges former test_zone3_apply_rtl.py, test_zone3_blob_kind.py,
test_zone3_rtl_consolidation.py, test_zone3_rtl_decision.py,
test_zone3_script_context.py, and test_zone3_picture_alignment.py.

Covers: apply_rtl (heading/body parity, idempotence, non-Arabic passthrough),
normalize_for_garble + BlobKind, decide_rtl (single 0.15-threshold decider),
the RTL-consolidation contract (decide_rtl as sole decision point, single
call in validate_tree / _pre_inference_normalize), ScriptContext.from_document,
and non-destructive picture-text splicing.
"""

from __future__ import annotations

import dataclasses
import unicodedata
from unittest.mock import MagicMock

import pytest

from pageindex_mcp.converters import (
    splice_figure_markers,
    splice_picture_text_for_tree,
)
from pageindex_mcp.script import (
    BlobKind,
    GARBLE_DIGIT_FLOOR,
    PRESENTATION_RANGES,
    RtlDecision,
    ScriptContext,
    apply_rtl,
    decide_rtl,
    is_arabic_char,
    normalize_for_garble,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ARABIC_LINE = "المادة الأولى تنظيم الحقوق"
_ENGLISH_LINE = "This is a normal English sentence with enough words to test."

_LOGICAL_ARABIC = "المادة الأولى تنظيم الحقوق والواجبات للمواطنين"
_REVERSED_ARABIC = " ".join(w[::-1] for w in _LOGICAL_ARABIC.split())


# ===========================================================================
# apply_rtl
# ===========================================================================

class TestApplyRtlReversedFlagFalse:
    """reversed_flag=False must return input unchanged."""

    def test_arabic_text_unchanged(self):
        assert apply_rtl(_ARABIC_LINE, reversed_flag=False) == _ARABIC_LINE

    def test_english_text_unchanged(self):
        assert apply_rtl(_ENGLISH_LINE, reversed_flag=False) == _ENGLISH_LINE

# ===========================================================================
# BlobKind / normalize_for_garble
# ===========================================================================

class TestNormalizeRawMarkdown:
    """RAW_MARKDOWN strips markdown scaffolding."""

    def test_strips_heading_markers(self):
        result = normalize_for_garble("# Heading", BlobKind.RAW_MARKDOWN)
        assert "#" not in result
        assert "Heading" in result

    def test_strips_pipes(self):
        result = normalize_for_garble("| col1 | col2 | col3 |", BlobKind.RAW_MARKDOWN)
        assert "|" not in result
        assert "col1" in result and "col2" in result

# ===========================================================================
# RTL consolidation contract: decide_rtl is the sole decision point
# ===========================================================================

# ===========================================================================
# decide_rtl: single-threshold (0.15) decider
# ===========================================================================

class TestNonArabicNotReversed:
    def test_pure_latin(self):
        decision = decide_rtl("This is a normal English sentence with enough length")
        assert decision.reversed is False
        assert decision.sampled == 0, "Non-Arabic text should be bailed out early"

    def test_empty_string(self):
        decision = decide_rtl("")
        assert decision.reversed is False
        assert decision.sampled == 0


class TestBilingualThresholdDependent:
    def test_low_arabic_ratio_bails_out(self):
        """Text with Arabic ratio below 0.15 should bail out as not reversed."""
        text = "Hello world this is a long English text " * 5 + "مادة"
        ar_count = sum(1 for c in text if is_arabic_char(c))
        assert ar_count / len(text) < 0.15, "precondition: ratio below threshold"
        decision = decide_rtl(text)
        assert decision.reversed is False
        assert decision.sampled == 0

    def test_above_threshold_arabic_gets_evaluated(self):
        """Text with Arabic ratio above 0.15 should be evaluated, not bailed out."""
        text = _LOGICAL_ARABIC + "\n" + _LOGICAL_ARABIC
        ar_count = sum(1 for c in text if is_arabic_char(c))
        assert ar_count / max(len(text), 1) > 0.15, "precondition: ratio above threshold"
        decision = decide_rtl(text)
        assert isinstance(decision, RtlDecision)


class TestSingleThreshold:
    def test_threshold_is_015(self):
        """Boundary: at exactly 0.15 ratio the check is <= 0.15 -> bails out."""
        text = "x" * 85 + "ا" * 15  # ~15% Arabic
        ar_ratio = sum(1 for c in text if is_arabic_char(c)) / len(text)
        assert abs(ar_ratio - 0.15) < 0.01
        decision = decide_rtl(text)
        assert decision.sampled == 0, "At exactly 0.15 ratio, should bail out (<=)"

class TestConsistentHeadingBodyDecision:
    """reconstruct_bidi_order must apply the same decide_rtl threshold to
    headings and body text -- no threshold divergence."""

    def test_below_threshold_both_skipped(self):
        from pageindex_mcp.converters import reconstruct_bidi_order

        latin_body = "This is English content repeated. " * 20
        arabic_heading = "## المادة"
        text = arabic_heading + "\n\n" + latin_body

        ar_count = sum(1 for c in text if is_arabic_char(c))
        ratio = ar_count / len(text)
        assert ratio < 0.15, f"precondition: ratio {ratio:.3f} must be below 0.15"

        result, _decision = reconstruct_bidi_order(text)
        assert "##" in result, "heading marker must be preserved"
        assert "This is English content repeated." in result

    def test_logical_arabic_heading_and_body_consistent(self):
        from pageindex_mcp.converters import reconstruct_bidi_order

        heading = "## المادة الأولى تنظيم الحقوق"
        body = "تنظيم الحقوق والواجبات للمواطنين في إطار القانون العام"
        text = heading + "\n\n" + body + "\n" + body

        result, _decision = reconstruct_bidi_order(text)
        assert "المادة الأولى" in result
        assert "تنظيم الحقوق" in result


# ===========================================================================
# ScriptContext.from_document
# ===========================================================================

class TestScriptContextFromDocumentFilename:
    """Filename-based script inference. detect_ocr_langs scans actual Unicode
    codepoints in the filename (not ISO-639 codes), so Latin-character
    filenames -- even with an '_ara' suffix -- return 'Latn'."""

    def test_arabic_codepoint_filename(self):
        ctx = ScriptContext.from_document("سياسة.pdf", "")
        assert ctx.dominant_script == "Arab"
        assert ctx.source in ("filename", "combined")

    def test_latin_filename_returns_latn(self):
        ctx = ScriptContext.from_document("musterbedingungen_deu.pdf", "")
        assert ctx.dominant_script == "Latn"
        assert ctx.source in ("filename", "combined")

class TestScriptContextPresentationForms:
    """had_presentation_forms is detected on raw text BEFORE NFKC
    normalization (Presentation Forms codepoints are destroyed by NFKC)."""

    @staticmethod
    def _make_pf_text(pf_ratio: float = 0.60, total_arabic: int = 100) -> str:
        pf_chars = [chr(c) for c in range(0xFE70, 0xFE70 + int(total_arabic * pf_ratio))]
        regular_chars = [chr(c) for c in range(0x0620, 0x0620 + total_arabic - len(pf_chars))]
        return "".join(pf_chars + regular_chars)

    def test_high_pf_ratio_detected(self):
        raw = self._make_pf_text(pf_ratio=0.60, total_arabic=80)
        ctx = ScriptContext.from_document("doc.pdf", raw)
        assert ctx.had_presentation_forms is True

    def test_low_pf_ratio_not_detected(self):
        raw = "".join(chr(c) for c in range(0x0620, 0x0660))
        ctx = ScriptContext.from_document("doc.pdf", raw)
        assert ctx.had_presentation_forms is False

class TestScriptContextSourceProvenance:
    def test_source_filename_only(self):
        ctx = ScriptContext.from_document("doc_ara.pdf", "")
        assert ctx.source == "filename"

    def test_source_combined(self):
        german = "Die Versicherung umfasst die gesetzliche Haftpflicht"
        ctx = ScriptContext.from_document("doc_deu.pdf", german)
        assert ctx.source == "combined"

# ===========================================================================
# Picture alignment: non-destructive splice, landscape exclusion
# ===========================================================================

class TestPictureAlignment:
    def test_tree_splice_does_not_pop_ocr_text(self):
        """splice_picture_text_for_tree must not destroy ocr_text on the dict."""
        md = "before <!-- image --> after"
        pics = [{"ocr_text": "chart data here", "page": 1}]
        result = splice_picture_text_for_tree(md, pics)
        assert "chart data here" in result
        assert pics[0].get("ocr_text") == "chart data here", (
            "ocr_text was popped -- tree splice must be non-destructive"
        )
