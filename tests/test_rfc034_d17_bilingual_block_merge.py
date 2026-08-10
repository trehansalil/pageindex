"""RFC-034 D17: MOU bilingual block-merging guards.

Two guards are under test:

1. `_repair_docling_tables` must not collapse an all-identical pipe-table row
   when the shared cell value is mixed-script (Arabic + Latin) -- such rows are
   legitimate bilingual data, not a Docling merge artefact.
2. The D3 `reconstruct_bidi_order` re-normalization pass must be skipped when a
   document's Latin-character fraction exceeds `_BIDI_RENORM_LATIN_GUARD`.
"""

import pytest

from pageindex_mcp.client import (
    _BIDI_RENORM_LATIN_GUARD,
    _latin_fraction,
    _renormalize_bidi_guarded,
)
from pageindex_mcp.converters import _repair_docling_tables

# --------------------------------------------------------------------------
# Guard 1: _repair_docling_tables mixed-script row
# --------------------------------------------------------------------------


def test_mixed_script_degenerate_row_is_not_collapsed():
    """An all-identical row whose shared value is Arabic+Latin survives intact."""
    shared = "Nafis نافس"
    md = (
        "| A | B | C | D |\n"
        "| --- | --- | --- | --- |\n"
        f"| {shared} | {shared} | {shared} | {shared} |\n"
    )
    out = _repair_docling_tables(md, "mou.pdf")
    lines = out.strip().split("\n")
    assert lines[-1] == f"| {shared} | {shared} | {shared} | {shared} |"
    # Not degraded to a single-cell row.
    assert f"| {shared} |" not in lines


def test_latin_only_degenerate_row_still_collapses():
    """The guard must not disable the RFC-029 D4 collapse for single-script rows.

    RFC-035 D0: the first post-separator row is exempt from collapse
    (Docling repeated-label guard), independent of the D17 mixed-script
    guard tested here -- so a distinct leading row precedes the degenerate
    one to isolate the two guards.
    """
    md = (
        "| x | y | z | w |\n"
        "| --- | --- | --- | --- |\n"
        "| p | q | r | s |\n"
        "| Yes | Yes | Yes | Yes |\n"
    )
    out = _repair_docling_tables(md, "eng.pdf")
    assert "| Yes |" in out
    assert "| Yes | Yes | Yes | Yes |" not in out


def test_arabic_only_degenerate_row_still_collapses():
    """Arabic without Latin is single-script -- the guard must not fire.

    RFC-035 D0: the first post-separator row is exempt from collapse
    (Docling repeated-label guard), independent of the D17 mixed-script
    guard tested here -- so a distinct leading row precedes the degenerate
    one to isolate the two guards.
    """
    md = (
        "| a | b | c | d |\n"
        "| --- | --- | --- | --- |\n"
        "| لا | لا | لا | لا |\n"
        "| نعم | نعم | نعم | نعم |\n"
    )
    out = _repair_docling_tables(md, "ar.pdf")
    assert "| نعم |" in out
    assert "| نعم | نعم | نعم | نعم |" not in out


def test_mixed_script_row_below_col_threshold_is_untouched():
    """Narrow rows never reach the collapse branch; content is still preserved."""
    md = "| a | b |\n| --- | --- |\n| Nafis نافس | Nafis نافس |\n"
    out = _repair_docling_tables(md, "mou.pdf")
    assert "| Nafis نافس | Nafis نافس |" in out


def test_mixed_script_guard_preserves_all_content():
    """Content-preservation invariant: no non-whitespace character is lost."""
    shared = "MOHRE وزارة"
    md = f"| {shared} |  {shared}  | {shared} | {shared} |\n"
    out = _repair_docling_tables(md, "mou.pdf")
    assert out.count(shared) == 4


# --------------------------------------------------------------------------
# Guard 2: bilingual bidi re-normalization skip
# --------------------------------------------------------------------------


def test_latin_fraction_counts_ascii_alpha_only():
    assert _latin_fraction("abcd") == pytest.approx(1.0)
    assert _latin_fraction("") == 0.0
    # Digits, punctuation and Arabic are not Latin.
    assert _latin_fraction("1234") == 0.0
    assert _latin_fraction("نافس") == 0.0
    assert _latin_fraction("ab نص") == pytest.approx(2 / 5)


def test_bilingual_markdown_skips_renormalization(monkeypatch):
    """A Latin-heavy bilingual document must bypass reconstruct_bidi_order."""
    calls = []

    def _spy(text):
        calls.append(text)
        return "REORDERED"

    monkeypatch.setattr("pageindex_mcp.client.reconstruct_bidi_order", _spy)

    md = "## Memorandum of Understanding MOHRE and Nafis\n\nمذكرة تفاهم\n"
    assert _latin_fraction(md) > _BIDI_RENORM_LATIN_GUARD
    out = _renormalize_bidi_guarded(md, "mou.pdf")

    assert calls == [], "reconstruct_bidi_order must be skipped for bilingual docs"
    assert out == md


def test_arabic_dominant_markdown_still_renormalizes(monkeypatch):
    """The guard must not disable D3 on Arabic-dominant documents."""
    calls = []

    def _spy(text):
        calls.append(text)
        return "REORDERED"

    monkeypatch.setattr("pageindex_mcp.client.reconstruct_bidi_order", _spy)

    md = "## مذكرة تفاهم بين وزارة الموارد البشرية والتوطين وبرنامج نافس\n"
    assert _latin_fraction(md) <= _BIDI_RENORM_LATIN_GUARD
    out = _renormalize_bidi_guarded(md, "ar.pdf")

    assert calls == [md]
    assert out == "REORDERED"


def test_guard_threshold_is_thirty_percent():
    """Threshold is the design-specified 0.30; changing it is a decision, not a tweak."""
    assert _BIDI_RENORM_LATIN_GUARD == 0.30
