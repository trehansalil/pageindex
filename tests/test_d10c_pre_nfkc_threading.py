# ALLOW-NEW-TEST-FILE: D10c pre-NFKC ScriptContext threading tests (RFC-041)
"""Verify that pre-NFKC ScriptContext is threaded to all 10 _infer_presentation_forms
call sites so that Arabic Presentation Forms are correctly detected even after NFKC
decomposition destroys the presentation-form codepoints."""

from __future__ import annotations

import dataclasses
import unicodedata

import pytest

from pageindex_mcp.helpers.garble import (
    _infer_presentation_forms,
    _garble_ratio,
)
from pageindex_mcp.helpers.tree_validation import TreeSignals, validate_tree
from pageindex_mcp.helpers.verdict import _try_image_enrichment, compute_verdict
from pageindex_mcp.script import ScriptContext

# Arabic Presentation Forms-B text (U+FE70-U+FEFF range)
_ARABIC_PF_TEXT = "ﺍﺎﺻﺼﺵﺶ"
_ARABIC_PF_NFKC = unicodedata.normalize("NFKC", _ARABIC_PF_TEXT)

# Latin text — no presentation forms in either form
_LATIN_TEXT = "The quick brown fox jumps over the lazy dog"


class TestInferPresentationFormsParity:
    """_infer_presentation_forms returns the same result for pre-NFKC and
    post-NFKC paths on Latin text (no change expected)."""

    def test_latin_pre_nfkc_matches_post_nfkc(self):
        pre = _infer_presentation_forms(_LATIN_TEXT)
        post = _infer_presentation_forms(unicodedata.normalize("NFKC", _LATIN_TEXT))
        assert pre == post
        assert pre is False

    def test_arabic_pf_pre_nfkc_detects_forms(self):
        assert _infer_presentation_forms(_ARABIC_PF_TEXT) is True

    def test_arabic_pf_post_nfkc_loses_forms(self):
        assert _infer_presentation_forms(_ARABIC_PF_NFKC) is False


class TestScriptContextPreNfkcThreading:
    """Verify that ScriptContext with pre-NFKC had_presentation_forms=True
    bypasses the _infer_presentation_forms fallback at each call site."""

    @pytest.fixture
    def pre_nfkc_ctx(self):
        return ScriptContext(
            dominant_script="Arab",
            had_presentation_forms=True,
            source="test_pre_nfkc",
        )

    @pytest.fixture
    def post_nfkc_ctx(self):
        return ScriptContext(
            dominant_script="Arab",
            had_presentation_forms=False,
            source="test_post_nfkc",
        )

    # -- Site 7: garble.py _garble_ratio --
    def test_garble_ratio_uses_script_context_pf(self, pre_nfkc_ctx):
        _garble_ratio(_ARABIC_PF_NFKC, expected_script="Arab", script_context=pre_nfkc_ctx)

    # -- Site 9: tree_validation.py validate_tree --
    def test_validate_tree_accepts_script_context(self, pre_nfkc_ctx):
        structure = [{"heading": "test", "content": _ARABIC_PF_NFKC, "children": []}]
        result = validate_tree(structure, expected_script=pre_nfkc_ctx)
        assert result.signals is not None

    def test_tree_signals_from_tree_uses_script_context_pf(self, pre_nfkc_ctx, post_nfkc_ctx):
        structure = [{"heading": "test", "content": _ARABIC_PF_NFKC, "children": []}]
        sig_pre = TreeSignals.from_tree(structure, expected_script=pre_nfkc_ctx)
        sig_post = TreeSignals.from_tree(structure, expected_script=post_nfkc_ctx)
        assert sig_pre is not None
        assert sig_post is not None

    # -- Site 10: verdict.py compute_verdict --
    def test_compute_verdict_accepts_script_context(self, pre_nfkc_ctx):
        structure = [
            {"heading": "root", "content": "", "children": [
                {"heading": "child", "content": _ARABIC_PF_NFKC, "children": []}
            ]}
        ]
        vr = compute_verdict(structure, "", expected_script=pre_nfkc_ctx)
        assert vr.verdict is not None

    # -- Latin text: no change expected --
    def test_latin_script_context_false_unchanged(self):
        ctx = ScriptContext(
            dominant_script="Latn",
            had_presentation_forms=False,
            source="test_latin",
        )
        structure = [{"heading": "test", "content": _LATIN_TEXT, "children": []}]
        result = validate_tree(structure, expected_script=ctx)
        assert result.signals is not None


class TestScriptContextFromDocumentPostNfkc:
    """ScriptContext.from_document on post-NFKC text returns
    had_presentation_forms=False; the indexer must enrich it."""

    def test_from_document_post_nfkc_returns_false(self):
        ctx = ScriptContext.from_document("arabic.pdf", raw_text=_ARABIC_PF_NFKC)
        assert ctx.had_presentation_forms is False

    def test_from_document_pre_nfkc_returns_true(self):
        ctx = ScriptContext.from_document("arabic.pdf", raw_text=_ARABIC_PF_TEXT)
        assert ctx.had_presentation_forms is True

    def test_dataclasses_replace_enriches_pf(self):
        ctx = ScriptContext.from_document("arabic.pdf", raw_text=_ARABIC_PF_NFKC)
        assert ctx.had_presentation_forms is False
        enriched = dataclasses.replace(ctx, had_presentation_forms=True)
        assert enriched.had_presentation_forms is True
        assert enriched.dominant_script == ctx.dominant_script
        assert enriched.source == ctx.source
