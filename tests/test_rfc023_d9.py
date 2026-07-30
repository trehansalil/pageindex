"""Tests for RFC-023 Task 5.1 (D9): heading-marker BiDi preservation in
``reconstruct_bidi_order``.

Validates Design Property 9 (design-rfc023-run6-content-recovery-and-verdict-hardening.md):
even when whole-document reordering is skipped (Arabic ratio <=0.15, or the body
is already in logical order), a leading markdown heading marker's text is still
individually corrected via ``_BIDI_HEADING_PREFIX_RE`` so bilingual documents
don't lose heading structure. Documents with zero Arabic characters are
returned untouched (early return, perf preserved).
"""

from bidi.algorithm import get_display

from pageindex_mcp.converters import reconstruct_bidi_order

_LOGICAL_HEADING = "الفصل الأول: تعريفات"
_VISUAL_HEADING = get_display(_LOGICAL_HEADING)

_LOGICAL_BODY_LINE = (
    "هذا النص العربي مكتوب بترتيب منطقي صحيح تماما ويجب ان يبقى كما هو دون اي تغيير في الحروف"
)


class TestBidiHeadingPreservation:
    def test_bilingual_doc_arabic_heading_preserved(self):
        """Bilingual doc: Arabic ratio over the whole text is low (English body
        dominates), so full-document reorder is skipped, but the heading's
        Arabic text is still individually corrected to logical order."""
        body_en = "This is the English body text describing the agreement terms in detail. " * 5
        doc = "## " + _VISUAL_HEADING + "\n" + body_en
        result = reconstruct_bidi_order(doc)
        lines = result.splitlines()
        assert lines[0] == "## " + _LOGICAL_HEADING
        assert body_en in result

    def test_pure_english_doc_early_return(self):
        """Zero Arabic characters -> early return of the same text object,
        preserving the perf optimization (no per-line splitting/rejoining)."""
        doc = "Just plain english text with no arabic at all."
        result = reconstruct_bidi_order(doc)
        assert result is doc

    def test_logical_order_arabic_skips_body_reorder_but_fixes_heading(self):
        """Body Arabic text is already in logical order (detected via
        ``_text_is_logical_order``), so full-document reorder is skipped and the
        body is left untouched -- but the heading, stored in visual order, is
        still individually corrected."""
        body = (_LOGICAL_BODY_LINE + "\n") * 3
        doc = "## " + _VISUAL_HEADING + "\n" + body
        result = reconstruct_bidi_order(doc)
        lines = result.splitlines()
        assert lines[0] == "## " + _LOGICAL_HEADING
        assert lines[1:] == body.splitlines()
