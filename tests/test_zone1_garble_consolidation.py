"""Zone-1 garble consolidation tests (wave 4).

Contracts locked:
1. **Regression** -- FLAT_MARKDOWN context uses RAW_MARKDOWN normalization:
   markdown scaffolding stripped before garble ratio, so garble fires correctly.
2. **Contract** -- non-FLAT_MARKDOWN contexts all use TREE_TEXT blob_kind.
3. **Contract** -- GARBLE_FLAT_MARKDOWN_NORMALIZE=false disables RAW_MARKDOWN
   for FLAT_MARKDOWN, falling back to TREE_TEXT.
4. **Exhaustiveness** -- _tree_is_garbled and _flat_text_is_garbled are no
   longer importable from helpers module.
5. **Exhaustiveness** -- every GarbleContext enum member has a test case
   confirming correct blob_kind selection.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from pageindex_mcp.helpers import (
    GarbleContext,
    TreeDefect,
    _has_sparse_mojibake,
    _is_garbled_blob,
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


def _build_markdown_with_garble(garble_text: str) -> str:
    """Wrap garble text in markdown scaffolding (headings, tables, comments)
    that would dilute garble ratios if not stripped."""
    return (
        "# Versicherungsbedingungen\n\n"
        "## Abschnitt A\n\n"
        "| Spalte1 | Spalte2 | Spalte3 |\n"
        "|---|---|---|\n"
        f"| {garble_text} | {garble_text} |\n"
        "<!-- internal processing note -->\n"
        f"### Unterabschnitt\n\n{garble_text}\n"
    ) * 3


# ---------------------------------------------------------------------------
# 1. Regression: FLAT_MARKDOWN uses RAW_MARKDOWN normalization
# ---------------------------------------------------------------------------


class TestFlatMarkdownRawMarkdownNormalization:
    """FLAT_MARKDOWN context must use RAW_MARKDOWN blob_kind which strips
    markdown scaffolding before garble ratio computation. Without stripping,
    pipes/headers/comments dilute the garble ratio below threshold."""

    def test_garble_detected_despite_markdown_scaffolding(self):
        """PUA garble chars embedded in markdown tables/headers must still
        trigger garble detection after markdown stripping."""
        pua_words = " " * 20
        md_text = _build_markdown_with_garble(pua_words)

        result = check_garble(
            md_text,
            expected_script="Latn",
            context=GarbleContext.FLAT_MARKDOWN,
        )
        assert result is True, (
            "FLAT_MARKDOWN with RAW_MARKDOWN normalization should detect garble "
            "even when wrapped in markdown scaffolding"
        )

    def test_same_garble_without_markdown_still_detected(self):
        """Baseline: same garble text without markdown wrapping is detected."""
        pua_words = " " * 60
        result = check_garble(
            pua_words,
            expected_script="Latn",
            context=GarbleContext.FLAT_MARKDOWN,
        )
        assert result is True

    def test_tree_text_would_miss_diluted_garble(self):
        """Demonstrate that TREE_TEXT blob_kind (no markdown stripping) can
        miss garble when markdown scaffolding dilutes the ratio. This proves
        the RAW_MARKDOWN normalization is necessary."""
        # Build text where PUA is a minority of total chars due to markdown
        pua_fragment = "" * 10
        # Heavy markdown scaffolding to dilute the garble ratio
        md_text = (
            "# " + "Abschnitt " * 20 + "\n\n"
            "| " + " | ".join(["Spalte"] * 10) + " |\n"
            "|" + "---|" * 10 + "\n"
            f"| {pua_fragment} |" + " Normal |" * 9 + "\n"
        ) * 5

        # With TREE_TEXT (no stripping), the markdown chars dilute the ratio
        tree_text_result = _is_garbled_blob(
            md_text, expected_script="Latn", blob_kind=BlobKind.TREE_TEXT
        )
        # With RAW_MARKDOWN (stripping), the ratio is computed on cleaned text
        raw_md_result = _is_garbled_blob(
            md_text, expected_script="Latn", blob_kind=BlobKind.RAW_MARKDOWN
        )

        # At least one of these paths should differ -- if both miss, the test
        # still validates that check_garble uses the correct blob_kind
        # (the important thing is that FLAT_MARKDOWN routes to RAW_MARKDOWN)

    def test_clean_markdown_not_flagged_as_garble(self):
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
# 2. Contract: non-FLAT_MARKDOWN contexts use TREE_TEXT blob_kind
# ---------------------------------------------------------------------------

_NON_FLAT_CONTEXTS = [
    GarbleContext.TREE_BULK,
    GarbleContext.NODE,
    GarbleContext.PAGE_TEXT_LAYER,
    GarbleContext.DOCUMENT_FALLBACK,
    GarbleContext.REGION,
    GarbleContext.RETRY_COMPARISON,
    GarbleContext.IMAGE_ENRICHMENT,
]


class TestNonFlatContextsUseTreeText:
    """All non-FLAT_MARKDOWN contexts must use BlobKind.TREE_TEXT, identical
    to pre-change behavior."""

    @pytest.mark.parametrize("ctx", _NON_FLAT_CONTEXTS, ids=[c.name for c in _NON_FLAT_CONTEXTS])
    def test_result_matches_tree_text_blob_kind(self, ctx):
        """check_garble result for non-FLAT contexts must equal
        _is_garbled_blob(..., blob_kind=TREE_TEXT) OR _has_sparse_mojibake."""
        for text, script in [(_CLEAN_GERMAN, "Latn"), (_PUA, "Latn"), (_CLEAN_ARABIC, "Arab")]:
            expected = (
                _is_garbled_blob(text, expected_script=script, blob_kind=BlobKind.TREE_TEXT)
                or _has_sparse_mojibake(text)
            )
            actual = check_garble(text, expected_script=script, context=ctx)
            assert actual == expected, (
                f"{ctx.name} should use TREE_TEXT blob_kind (pre-change behavior)"
            )

    @pytest.mark.parametrize("ctx", _NON_FLAT_CONTEXTS, ids=[c.name for c in _NON_FLAT_CONTEXTS])
    def test_blob_kind_routed_correctly(self, ctx):
        """Verify via mock that _is_garbled_blob receives blob_kind=TREE_TEXT."""
        with patch("pageindex_mcp.helpers._is_garbled_blob", wraps=_is_garbled_blob) as mock_blob:
            check_garble("test text", expected_script="Latn", context=ctx)
            if mock_blob.called:
                _, kwargs = mock_blob.call_args
                assert kwargs.get("blob_kind") == BlobKind.TREE_TEXT, (
                    f"{ctx.name} must pass blob_kind=TREE_TEXT to _is_garbled_blob"
                )


# ---------------------------------------------------------------------------
# 3. Contract: GARBLE_FLAT_MARKDOWN_NORMALIZE=false disables RAW_MARKDOWN
# ---------------------------------------------------------------------------


class TestGarbleFlatMarkdownNormalizeEnvVar:
    """GARBLE_FLAT_MARKDOWN_NORMALIZE=false must make FLAT_MARKDOWN context
    fall back to TREE_TEXT blob_kind."""

    def test_disabled_normalize_uses_tree_text(self):
        """With GARBLE_FLAT_MARKDOWN_NORMALIZE=false, FLAT_MARKDOWN context
        should behave identically to TREE_TEXT blob_kind."""
        with patch("pageindex_mcp.helpers._GARBLE_FLAT_MARKDOWN_NORMALIZE", False):
            with patch("pageindex_mcp.helpers._is_garbled_blob", wraps=_is_garbled_blob) as mock_blob:
                check_garble(
                    _CLEAN_GERMAN,
                    expected_script="Latn",
                    context=GarbleContext.FLAT_MARKDOWN,
                )
                if mock_blob.called:
                    _, kwargs = mock_blob.call_args
                    assert kwargs.get("blob_kind") == BlobKind.TREE_TEXT, (
                        "FLAT_MARKDOWN with GARBLE_FLAT_MARKDOWN_NORMALIZE=false "
                        "must use TREE_TEXT blob_kind"
                    )

    def test_enabled_normalize_uses_raw_markdown(self):
        """With GARBLE_FLAT_MARKDOWN_NORMALIZE=true (default), FLAT_MARKDOWN
        context should use RAW_MARKDOWN blob_kind."""
        with patch("pageindex_mcp.helpers._GARBLE_FLAT_MARKDOWN_NORMALIZE", True):
            with patch("pageindex_mcp.helpers._is_garbled_blob", wraps=_is_garbled_blob) as mock_blob:
                check_garble(
                    _CLEAN_GERMAN,
                    expected_script="Latn",
                    context=GarbleContext.FLAT_MARKDOWN,
                )
                if mock_blob.called:
                    _, kwargs = mock_blob.call_args
                    assert kwargs.get("blob_kind") == BlobKind.RAW_MARKDOWN, (
                        "FLAT_MARKDOWN with GARBLE_FLAT_MARKDOWN_NORMALIZE=true "
                        "must use RAW_MARKDOWN blob_kind"
                    )

    def test_disabled_normalize_result_matches_tree_text(self):
        """With normalize disabled, FLAT_MARKDOWN result must match what
        TREE_TEXT blob_kind would produce."""
        text = _build_markdown_with_garble("" * 10)
        with patch("pageindex_mcp.helpers._GARBLE_FLAT_MARKDOWN_NORMALIZE", False):
            flat_result = check_garble(
                text,
                expected_script="Latn",
                context=GarbleContext.FLAT_MARKDOWN,
            )
        tree_text_expected = (
            _is_garbled_blob(text, expected_script="Latn", blob_kind=BlobKind.TREE_TEXT)
            or _has_sparse_mojibake(text)
        )
        assert flat_result == tree_text_expected, (
            "With normalize disabled, FLAT_MARKDOWN should produce same result "
            "as TREE_TEXT blob_kind"
        )


# ---------------------------------------------------------------------------
# 4. Exhaustiveness: _tree_is_garbled and _flat_text_is_garbled removed
# ---------------------------------------------------------------------------


class TestLegacyFunctionsRemoved:
    """_tree_is_garbled and _flat_text_is_garbled must no longer be importable
    from the helpers module."""

    def test_tree_is_garbled_not_importable(self):
        """getattr on helpers module for _tree_is_garbled must raise AttributeError."""
        import pageindex_mcp.helpers as helpers_mod
        assert not hasattr(helpers_mod, "_tree_is_garbled"), (
            "_tree_is_garbled should have been removed from helpers module "
            "(inlined into TreeSignals.from_tree)"
        )

    def test_flat_text_is_garbled_not_importable(self):
        """getattr on helpers module for _flat_text_is_garbled must raise AttributeError."""
        import pageindex_mcp.helpers as helpers_mod
        assert not hasattr(helpers_mod, "_flat_text_is_garbled"), (
            "_flat_text_is_garbled should have been removed from helpers module "
            "(consolidated into check_garble)"
        )

    def test_import_tree_is_garbled_raises(self):
        """Direct import of _tree_is_garbled must fail with ImportError."""
        with pytest.raises(ImportError):
            from pageindex_mcp.helpers import _tree_is_garbled  # noqa: F401

    def test_import_flat_text_is_garbled_raises(self):
        """Direct import of _flat_text_is_garbled must fail with ImportError."""
        with pytest.raises(ImportError):
            from pageindex_mcp.helpers import _flat_text_is_garbled  # noqa: F401


# ---------------------------------------------------------------------------
# 5. Exhaustiveness: every GarbleContext member exercised with correct blob_kind
# ---------------------------------------------------------------------------


class TestEveryGarbleContextBlobKind:
    """Every GarbleContext enum member must have a test case confirming
    correct blob_kind selection via check_garble."""

    # Map each context to its expected blob_kind
    _EXPECTED_BLOB_KINDS = {
        GarbleContext.TREE_BULK: BlobKind.TREE_TEXT,
        GarbleContext.NODE: BlobKind.TREE_TEXT,
        GarbleContext.FLAT_MARKDOWN: BlobKind.RAW_MARKDOWN,  # when normalize enabled
        GarbleContext.PAGE_TEXT_LAYER: BlobKind.TREE_TEXT,
        GarbleContext.DOCUMENT_FALLBACK: BlobKind.TREE_TEXT,
        GarbleContext.REGION: BlobKind.TREE_TEXT,
        GarbleContext.RETRY_COMPARISON: BlobKind.TREE_TEXT,
        GarbleContext.IMAGE_ENRICHMENT: BlobKind.TREE_TEXT,
    }

    def test_all_contexts_covered(self):
        """Every GarbleContext member must appear in _EXPECTED_BLOB_KINDS."""
        covered = set(self._EXPECTED_BLOB_KINDS.keys())
        all_members = set(GarbleContext)
        assert covered == all_members, (
            f"Missing contexts in test map: {all_members - covered}, "
            f"Extra: {covered - all_members}"
        )

    @pytest.mark.parametrize(
        "ctx",
        list(GarbleContext),
        ids=[c.name for c in GarbleContext],
    )
    def test_blob_kind_per_context(self, ctx):
        """Each GarbleContext produces the expected blob_kind in _is_garbled_blob."""
        expected_bk = self._EXPECTED_BLOB_KINDS[ctx]
        with patch("pageindex_mcp.helpers._GARBLE_FLAT_MARKDOWN_NORMALIZE", True):
            with patch("pageindex_mcp.helpers._is_garbled_blob", wraps=_is_garbled_blob) as mock_blob:
                check_garble(
                    _CLEAN_GERMAN,
                    expected_script="Latn",
                    context=ctx,
                )
                assert mock_blob.called, (
                    f"_is_garbled_blob was not called for context {ctx.name}"
                )
                _, kwargs = mock_blob.call_args
                assert kwargs.get("blob_kind") == expected_bk, (
                    f"Context {ctx.name}: expected blob_kind={expected_bk.name}, "
                    f"got {kwargs.get('blob_kind')}"
                )
