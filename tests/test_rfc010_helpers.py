"""Unit tests for RFC-010 corpus gap remediation: helpers.py deliverables D3A, D3B, D4."""

from pageindex_mcp.helpers import (
    _flat_text_is_garbled,
    _looks_like_toc_page,
    _tree_is_garbled,
)

# ── D3A: _tree_is_garbled ───────────────────────────────────────────────────


def test_pua_heavy_string_garbled():
    """PUA-char ratio > 3% (font/CMap mojibake) must flag the tree as garbled."""
    nodes = [
        {
            "title": "X",
            "text": "" * 5 + "a" * 90,
            "nodes": [
                {"title": "Y", "text": "" * 5 + "b" * 90, "nodes": []},
            ],
        }
    ]
    assert _tree_is_garbled(nodes) is True


def test_digit_junk_garbled():
    """Digit ratio > 60% on a blob > 500 chars flags numeric-junk garbling."""
    digit_text = "1651001429 " * 80  # 880 chars, ~91% digits
    nodes = [
        {
            "title": "A",
            "text": digit_text,
            "nodes": [
                {"title": "B", "text": "some text", "nodes": []},
            ],
        }
    ]
    assert _tree_is_garbled(nodes) is True


def test_single_word_repetition_garbled():
    """Single-token repetition > 30% of all tokens flags garbled repetition."""
    repeated = "AAAA " * 40 + "word1 word2 word3 " * 10  # 40/~70 tokens = ~57%
    nodes = [
        {
            "title": "T",
            "text": repeated,
            "nodes": [
                {"title": "C", "text": "ok", "nodes": []},
            ],
        }
    ]
    assert _tree_is_garbled(nodes) is True


def test_normal_german_text_not_garbled():
    """Realistic German insurance prose must not trip any garble heuristic."""
    german = (
        "Der Versicherungsschutz erstreckt sich auf alle versicherten Personen "
        "im vereinbarten Umfang. Die Beitragszahlung erfolgt jaehrlich im Voraus "
        "zum Beginn des Versicherungsjahres. "
    ) * 5
    nodes = [
        {
            "title": "Allgemeines",
            "text": german,
            "nodes": [
                {"title": "Geltungsbereich", "text": german, "nodes": []},
                {"title": "Leistungen", "text": german, "nodes": []},
            ],
        }
    ]
    assert _tree_is_garbled(nodes) is False


def test_latin_substitution_not_garbled():
    """~2% accented-Latin substitution (like doc b1a72fb2) stays under all thresholds."""
    normal = (
        "Der Versicherungsschutz erstreckt sich auf alle versicherten Personen "
        "im vereinbarten Umfang der Bedingungen. Beitragszahlung erfolgt jaehrlich "
        "im Voraus zum Beginn des laufenden Versicherungsjahres nach Vertragsschluss. "
    ) * 10  # ~1900 chars, varied tokens
    text = normal + "àéîõü" * 8  # 40 accented chars out of ~1940 = ~2%
    nodes = [
        {
            "title": "AVB",
            "text": text,
            "nodes": [
                {"title": "§1", "text": "Geltungsbereich", "nodes": []},
            ],
        }
    ]
    assert _tree_is_garbled(nodes) is False


# ── D3B: _flat_text_is_garbled ──────────────────────────────────────────────


def test_flat_text_pua_garbled():
    """Flat-path mirror of the PUA-ratio heuristic on a raw markdown string."""
    md = "" * 5 + "a" * 90 + "" * 5 + "b" * 90  # 10/200 = 5% PUA
    assert _flat_text_is_garbled(md) is True


def test_flat_text_digit_junk_garbled():
    """Flat-path mirror of the digit-ratio heuristic on a raw markdown string."""
    md = "1651001429 " * 80  # ~880 chars, >60% digits
    assert _flat_text_is_garbled(md) is True


def test_flat_text_normal_not_garbled():
    """Normal prose passed straight through the flat-path gate stays clean."""
    md = "Der Versicherungsschutz erstreckt sich auf alle versicherten Personen.\n" * 10
    assert _flat_text_is_garbled(md) is False


# ── D4: _looks_like_toc_page ────────────────────────────────────────────────


def test_dot_leader_block_is_toc():
    """A block where every line is a dot-leader entry is classified as a TOC page."""
    toc = (
        "Introduction ....... 1\n"
        "Methodology ........ 5\n"
        "Results ............ 12\n"
        "Conclusion ......... 40\n"
        "References ......... 45\n"
    )
    assert _looks_like_toc_page(toc) is True


def test_dot_leader_pipe_table_is_toc():
    """Pipe-table-wrapped dot-leader lines still match (the D4 regex fix)."""
    toc_table = (
        "| Introduction ....... 1 |\n"
        "| Methodology ........ 5 |\n"
        "| Results ............ 12 |\n"
        "| Conclusion ......... 40 |\n"
    )
    assert _looks_like_toc_page(toc_table) is True


def test_normal_table_not_toc():
    """A regular data table without dot leaders must not be misclassified as a TOC."""
    table = (
        "| Tarif | Beitrag | Selbstbeteiligung |\n"
        "| --- | --- | --- |\n"
        "| Basis | 12 EUR | 100 EUR |\n"
        "| Komfort | 24 EUR | 50 EUR |\n"
    )
    assert _looks_like_toc_page(table) is False


def test_short_block_not_toc():
    """Fewer than 3 lines never qualifies as a TOC page, even with dot leaders."""
    short = "Chapter 1 ....... 1\nChapter 2 ....... 5\n"
    assert _looks_like_toc_page(short) is False
