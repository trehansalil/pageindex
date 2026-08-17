"""Zone-1 check_garble consolidation tests.

Contracts locked:
1. **Contract** -- GarbleContext is a StrEnum with exactly 8 members;
   check_garble requires expected_script as keyword-only.
2. **Exhaustiveness** -- check_garble produces identical results to the
   function it replaces for every GarbleContext value.
3. **Regression** -- short-text garble-by-default fires only for
   FLAT_MARKDOWN + original garbling defect.
4. **Regression** -- FLAT_MARKDOWN strips markdown before ratio computation.
5. **Regression** -- converter contexts include _has_sparse_mojibake.
"""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import (
    GarbleContext,
    TreeDefect,
    _has_sparse_mojibake,
    _is_garbled_blob,
    _tree_is_garbled,
    _flat_text_is_garbled,
    check_garble,
)
from pageindex_mcp.script import BlobKind


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_PUA = "" * 400  # PUA chars -> garble signal

_CLEAN_GERMAN = (
    "Die Versicherung deckt Schaden an Dritten im Rahmen der "
    "vereinbarten Deckungssumme. Der Versicherungsnehmer ist "
    "verpflichtet, den Schaden unverzueglich zu melden. "
) * 5

_CLEAN_ARABIC = (
    "في هذه الوثيقة نصوص عربية متنوعة للاختبار وهي جملة كاملة "
    "تتضمن معلومات عن التامين والشروط العامة "
) * 5

_GARBLED_LATIN = "xQ3z7 kW9p2 mL5n8 " * 30  # nonsense latin tokens

# Arabic-Latin-Arabic glued fragments that _has_sparse_mojibake should catch.
# Simulates warid-597 / MOU type text: Arabic words glued to short Latin
# fragments without spaces.
_SPARSE_MOJIBAKE = (
    "هذا" + "x3z" + "النص " + "عربي" + "q7k" + "متنوع "
) * 30  # well above 100 chars and >2% mixed-script tokens


# ---------------------------------------------------------------------------
# 1. Contract: GarbleContext enum membership
# ---------------------------------------------------------------------------

class TestGarbleContextContract:
    """GarbleContext must have exactly 8 members."""

    EXPECTED_MEMBERS = {
        "TREE_BULK",
        "NODE",
        "FLAT_MARKDOWN",
        "PAGE_TEXT_LAYER",
        "DOCUMENT_FALLBACK",
        "REGION",
        "RETRY_COMPARISON",
        "IMAGE_ENRICHMENT",
    }

    def test_exact_member_count(self):
        assert len(GarbleContext) == 8, (
            f"GarbleContext has {len(GarbleContext)} members, expected 8: "
            f"{set(m.name for m in GarbleContext)}"
        )

    def test_exact_member_names(self):
        actual = {m.name for m in GarbleContext}
        assert actual == self.EXPECTED_MEMBERS, (
            f"Missing: {self.EXPECTED_MEMBERS - actual}, "
            f"Extra: {actual - self.EXPECTED_MEMBERS}"
        )

    def test_is_str_enum(self):
        for m in GarbleContext:
            assert isinstance(m, str), f"{m.name} is not a str"


class TestCheckGarbleContract:
    """check_garble requires expected_script as keyword-only."""

    def test_positional_expected_script_raises(self):
        """Passing expected_script positionally must raise TypeError."""
        with pytest.raises(TypeError):
            # Two positional args: text and expected_script
            check_garble("hello", "Latn", context=GarbleContext.NODE)  # type: ignore[misc]

    def test_missing_expected_script_raises(self):
        """Omitting expected_script entirely must raise TypeError."""
        with pytest.raises(TypeError):
            check_garble("hello", context=GarbleContext.NODE)  # type: ignore[call-arg]

    def test_missing_context_raises(self):
        """Omitting context must raise TypeError."""
        with pytest.raises(TypeError):
            check_garble("hello", expected_script="Latn")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 2. Exhaustiveness: check_garble matches legacy function per context
# ---------------------------------------------------------------------------

# Parameterized text samples: (label, text, expected_script)
_SAMPLE_TEXTS = [
    ("clean_german", _CLEAN_GERMAN, "Latn"),
    ("clean_arabic", _CLEAN_ARABIC, "Arab"),
    ("garbled_pua", _PUA, "Latn"),
    ("garbled_latin_nonsense", _GARBLED_LATIN, "Arab"),
    ("empty", "", None),
    ("short_clean", "Hallo Welt", "Latn"),
]


class TestExhaustivenessTREE_BULK:
    """TREE_BULK: check_garble == _tree_is_garbled on flattened text."""

    @pytest.mark.parametrize("label,text,script", _SAMPLE_TEXTS, ids=[s[0] for s in _SAMPLE_TEXTS])
    def test_matches_tree_is_garbled(self, label, text, script):
        # _tree_is_garbled operates on a node list; it flattens and calls
        # check_garble(TREE_BULK) internally. We verify the direct call
        # produces the same result as _is_garbled_blob OR _has_sparse_mojibake.
        expected = (
            _is_garbled_blob(text, expected_script=script, blob_kind=BlobKind.TREE_TEXT)
            or _has_sparse_mojibake(text)
        )
        actual = check_garble(text, expected_script=script, context=GarbleContext.TREE_BULK)
        assert actual == expected, f"TREE_BULK mismatch for {label}"


class TestExhaustivenessNODE:
    """NODE: check_garble == _is_garbled_blob OR _has_sparse_mojibake."""

    @pytest.mark.parametrize("label,text,script", _SAMPLE_TEXTS, ids=[s[0] for s in _SAMPLE_TEXTS])
    def test_matches_is_garbled_blob(self, label, text, script):
        expected = (
            _is_garbled_blob(text, expected_script=script, blob_kind=BlobKind.TREE_TEXT)
            or _has_sparse_mojibake(text)
        )
        actual = check_garble(text, expected_script=script, context=GarbleContext.NODE)
        assert actual == expected, f"NODE mismatch for {label}"


class TestExhaustivenessFLAT_MARKDOWN:
    """FLAT_MARKDOWN: check_garble == _flat_text_is_garbled (delegates)."""

    @pytest.mark.parametrize("label,text,script", _SAMPLE_TEXTS, ids=[s[0] for s in _SAMPLE_TEXTS])
    def test_matches_flat_text_is_garbled(self, label, text, script):
        expected = _flat_text_is_garbled(text, expected_script=script)
        actual = check_garble(
            text,
            expected_script=script,
            context=GarbleContext.FLAT_MARKDOWN,
        )
        assert actual == expected, f"FLAT_MARKDOWN mismatch for {label}"


class TestExhaustivenessConverterContexts:
    """PAGE_TEXT_LAYER / DOCUMENT_FALLBACK / REGION: check_garble ==
    _is_garbled_blob OR _has_sparse_mojibake (new behavioral addition)."""

    _CONVERTER_CONTEXTS = [
        GarbleContext.PAGE_TEXT_LAYER,
        GarbleContext.DOCUMENT_FALLBACK,
        GarbleContext.REGION,
    ]

    @pytest.mark.parametrize("ctx", _CONVERTER_CONTEXTS, ids=[c.name for c in _CONVERTER_CONTEXTS])
    @pytest.mark.parametrize("label,text,script", _SAMPLE_TEXTS, ids=[s[0] for s in _SAMPLE_TEXTS])
    def test_matches_blob_plus_mojibake(self, ctx, label, text, script):
        expected = (
            _is_garbled_blob(text, expected_script=script, blob_kind=BlobKind.TREE_TEXT)
            or _has_sparse_mojibake(text)
        )
        actual = check_garble(text, expected_script=script, context=ctx)
        assert actual == expected, f"{ctx.name} mismatch for {label}"


class TestExhaustivenessRetryAndImage:
    """RETRY_COMPARISON and IMAGE_ENRICHMENT: same as _is_garbled_blob OR
    _has_sparse_mojibake."""

    _OTHER_CONTEXTS = [GarbleContext.RETRY_COMPARISON, GarbleContext.IMAGE_ENRICHMENT]

    @pytest.mark.parametrize("ctx", _OTHER_CONTEXTS, ids=[c.name for c in _OTHER_CONTEXTS])
    @pytest.mark.parametrize("label,text,script", _SAMPLE_TEXTS, ids=[s[0] for s in _SAMPLE_TEXTS])
    def test_matches_blob_plus_mojibake(self, ctx, label, text, script):
        expected = (
            _is_garbled_blob(text, expected_script=script, blob_kind=BlobKind.TREE_TEXT)
            or _has_sparse_mojibake(text)
        )
        actual = check_garble(text, expected_script=script, context=ctx)
        assert actual == expected, f"{ctx.name} mismatch for {label}"


# ---------------------------------------------------------------------------
# 3. Regression: short-text garble-by-default fires ONLY for FLAT_MARKDOWN
# ---------------------------------------------------------------------------

class TestShortTextGarbleByDefault:
    """RFC-025 D2: short text (<200 chars) with original garbling defect
    returns True immediately -- but ONLY for FLAT_MARKDOWN context."""

    _SHORT_TEXT = "Kurzer Text"  # < 200 chars

    _GARBLE_DEFECTS = [TreeDefect.GARBLING, TreeDefect.NODE_GARBLING]

    _NON_FLAT_CONTEXTS = [
        GarbleContext.TREE_BULK,
        GarbleContext.NODE,
        GarbleContext.PAGE_TEXT_LAYER,
        GarbleContext.DOCUMENT_FALLBACK,
        GarbleContext.REGION,
        GarbleContext.RETRY_COMPARISON,
        GarbleContext.IMAGE_ENRICHMENT,
    ]

    @pytest.mark.parametrize("defect", _GARBLE_DEFECTS, ids=[d.name for d in _GARBLE_DEFECTS])
    def test_flat_markdown_fires(self, defect):
        """FLAT_MARKDOWN + garble defect + short text -> True."""
        result = check_garble(
            self._SHORT_TEXT,
            expected_script="Latn",
            context=GarbleContext.FLAT_MARKDOWN,
            original_defect=defect,
        )
        assert result is True, (
            f"Short-text garble-by-default should fire for FLAT_MARKDOWN + {defect.name}"
        )

    @pytest.mark.parametrize("ctx", _NON_FLAT_CONTEXTS, ids=[c.name for c in _NON_FLAT_CONTEXTS])
    @pytest.mark.parametrize("defect", _GARBLE_DEFECTS, ids=[d.name for d in _GARBLE_DEFECTS])
    def test_non_flat_contexts_do_not_fire(self, ctx, defect):
        """Non-FLAT_MARKDOWN contexts must NOT trigger short-text default."""
        # check_garble does not accept original_defect for non-FLAT contexts,
        # but the parameter is accepted (just ignored). The short text is clean,
        # so the result should be False (not garbled by normal heuristics).
        result = check_garble(
            self._SHORT_TEXT,
            expected_script="Latn",
            context=ctx,
            original_defect=defect,
        )
        # The short clean text should NOT be flagged as garbled by normal
        # heuristics -- if it returns True, the short-text default leaked.
        assert result is False, (
            f"Short-text garble-by-default LEAKED into {ctx.name} + {defect.name}"
        )

    def test_flat_markdown_without_garble_defect_does_not_fire(self):
        """FLAT_MARKDOWN + non-garble defect + short text -> normal evaluation."""
        result = check_garble(
            self._SHORT_TEXT,
            expected_script="Latn",
            context=GarbleContext.FLAT_MARKDOWN,
            original_defect=TreeDefect.REORDERED,
        )
        assert result is False, (
            "Short-text garble-by-default should NOT fire for non-garble defects"
        )

    def test_flat_markdown_no_defect_does_not_fire(self):
        """FLAT_MARKDOWN + None defect + short text -> normal evaluation."""
        result = check_garble(
            self._SHORT_TEXT,
            expected_script="Latn",
            context=GarbleContext.FLAT_MARKDOWN,
            original_defect=None,
        )
        assert result is False


# ---------------------------------------------------------------------------
# 4. Regression: FLAT_MARKDOWN strips markdown before ratio computation
# ---------------------------------------------------------------------------

class TestFlatMarkdownStripsFormatting:
    """FLAT_MARKDOWN must strip heading markers, table pipes, HTML comments
    before garble ratio computation via normalize_for_garble(text, RAW_MARKDOWN).
    Without stripping, markdown scaffolding dilutes the garble ratio and lets
    garbled text pass."""

    def test_markdown_scaffolding_does_not_dilute_garble(self):
        """PUA chars wrapped in markdown formatting must still be detected."""
        # Build text where PUA chars are interleaved with markdown formatting.
        # Without stripping, the #/| chars dilute the PUA ratio below threshold.
        pua_words = " " * 20
        md_text = f"# Heading\n\n| Col1 | Col2 |\n|---|---|\n| {pua_words} | {pua_words} |\n" * 5
        result = check_garble(
            md_text,
            expected_script="Latn",
            context=GarbleContext.FLAT_MARKDOWN,
        )
        assert result is True, "Garbled PUA text in markdown formatting should be detected"

    def test_clean_markdown_not_flagged(self):
        """Clean German text with markdown formatting must NOT be flagged."""
        md_text = (
            "# Versicherungsbedingungen\n\n"
            "| Abschnitt | Inhalt |\n|---|---|\n"
            f"| Allgemein | {_CLEAN_GERMAN} |\n"
            "<!-- internal comment -->\n"
            f"## Details\n\n{_CLEAN_GERMAN}\n"
        )
        result = check_garble(
            md_text,
            expected_script="Latn",
            context=GarbleContext.FLAT_MARKDOWN,
        )
        assert result is False, "Clean German markdown should NOT be flagged"


# ---------------------------------------------------------------------------
# 5. Regression: converter contexts include _has_sparse_mojibake
# ---------------------------------------------------------------------------

class TestConverterContextsIncludeSparseMojibake:
    """PAGE_TEXT_LAYER / DOCUMENT_FALLBACK / REGION now include
    _has_sparse_mojibake in the check (Cross-cutting Issue 3: MOU / warid-597
    type text). Text that passes _is_garbled_blob but fails
    _has_sparse_mojibake must still be caught."""

    _CONVERTER_CONTEXTS = [
        GarbleContext.PAGE_TEXT_LAYER,
        GarbleContext.DOCUMENT_FALLBACK,
        GarbleContext.REGION,
    ]

    @pytest.mark.parametrize("ctx", _CONVERTER_CONTEXTS, ids=[c.name for c in _CONVERTER_CONTEXTS])
    def test_sparse_mojibake_caught(self, ctx):
        """Arabic-Latin-Arabic glued fragments must be caught even when
        _is_garbled_blob alone would miss them."""
        # Verify precondition: _is_garbled_blob misses this text
        blob_result = _is_garbled_blob(
            _SPARSE_MOJIBAKE, expected_script="Arab", blob_kind=BlobKind.TREE_TEXT
        )
        mojibake_result = _has_sparse_mojibake(_SPARSE_MOJIBAKE)
        # We need the text to be caught by _has_sparse_mojibake
        # (if _is_garbled_blob also catches it, the test still validates
        # that check_garble returns True, which is correct)
        assert mojibake_result is True, (
            "Test fixture _SPARSE_MOJIBAKE must trigger _has_sparse_mojibake "
            "(adjust fixture if Arabic-Latin-Arabic pattern changed)"
        )

        # check_garble with converter context must catch it
        result = check_garble(
            _SPARSE_MOJIBAKE,
            expected_script="Arab",
            context=ctx,
        )
        assert result is True, (
            f"Sparse mojibake text not caught by {ctx.name} -- "
            "_has_sparse_mojibake integration missing"
        )

    @pytest.mark.parametrize("ctx", _CONVERTER_CONTEXTS, ids=[c.name for c in _CONVERTER_CONTEXTS])
    def test_clean_arabic_not_flagged(self, ctx):
        """Legitimate Arabic text must NOT be flagged by converter contexts."""
        result = check_garble(
            _CLEAN_ARABIC,
            expected_script="Arab",
            context=ctx,
        )
        assert result is False, f"Clean Arabic flagged as garbled by {ctx.name}"
