"""RFC-034 D14 -- reconstruct_bidi_order idempotence property test.

D3's markdown-level re-normalization safety net (RFC-034 D3) applies
`reconstruct_bidi_order` to remote-returned markdown before tree
construction. The existing node-level repair loop (client.py:1279-1298)
may re-apply `reconstruct_bidi_order` to the same content when
`validate_tree` flags `rtl_reversal`. D3's simpler design (option b --
rely on idempotence, no flag) is only safe if
`reconstruct_bidi_order(reconstruct_bidi_order(x)) == reconstruct_bidi_order(x)`
holds for every document PageIndex actually processes.

This test IS the deliverable per RFC-034 D14: pass -> D3 uses option (b);
fail -> D3 must fall back to option (a) (flag-based suppression).
"""

from pathlib import Path

import pytest

from pageindex_mcp.converters import reconstruct_bidi_order

_DOC_STORE = Path(__file__).resolve().parent.parent / "doc_store"
_CORPUS_MD_FILES = sorted(_DOC_STORE.rglob("*.md")) if _DOC_STORE.is_dir() else []


@pytest.mark.skipif(not _CORPUS_MD_FILES, reason="no .md files found under doc_store/")
@pytest.mark.parametrize("md_path", _CORPUS_MD_FILES, ids=lambda p: p.name)
def test_corpus_file_idempotent(md_path):
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    once = reconstruct_bidi_order(text)
    twice = reconstruct_bidi_order(once)
    assert twice == once


class TestEdgeCaseIdempotence:
    def test_empty_string(self):
        once = reconstruct_bidi_order("")
        assert reconstruct_bidi_order(once) == once

    def test_pure_latin(self):
        text = "This is plain English prose with no Arabic content at all."
        once = reconstruct_bidi_order(text)
        assert reconstruct_bidi_order(once) == once
        assert once == text

    def test_pure_arabic(self):
        text = "هذا نص عربي يجب ان يبقى قابلا للقراءة بعد اعادة الترتيب مرتين متتاليتين"
        once = reconstruct_bidi_order(text)
        assert reconstruct_bidi_order(once) == once

    def test_mixed_arabic_latin(self):
        text = (
            "# Section Title\n\n"
            "This document mixes English body text with Arabic: "
            "هذا نص عربي مضمن داخل نص انجليزي طويل بما يكفي لتفعيل اعادة الترتيب "
            "and continues in English afterwards."
        )
        once = reconstruct_bidi_order(text)
        assert reconstruct_bidi_order(once) == once

    def test_bidi_control_characters(self):
        rlm = "‏"
        lrm = "‎"
        rle = "‫"
        pdf = "‬"
        text = (
            f"# {rle}عنوان القسم{pdf}\n\n"
            f"{rlm}نص عربي يحتوي على محارف تحكم اتجاهية{lrm} mixed with Latin text "
            "that is long enough to matter for the reorder gate."
        )
        once = reconstruct_bidi_order(text)
        assert reconstruct_bidi_order(once) == once

    def test_heading_only_arabic(self):
        text = "## الفصل الأول: تعريفات"
        once = reconstruct_bidi_order(text)
        assert reconstruct_bidi_order(once) == once
