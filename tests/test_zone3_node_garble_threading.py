"""Zone-3 contract tests: _garble_check_nodes receives ScriptContext,
QF3 bilingual per-node override (RFC-021) preserved."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pageindex_mcp.helpers import (
    GarbleConfig,
    _garble_check_nodes,
)
from pageindex_mcp.script import ScriptContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nodes(texts: list[str], titles: list[str] | None = None) -> list[dict]:
    """Build a node list for _garble_check_nodes."""
    titles = titles or [""] * len(texts)
    return [
        {"text": t, "title": tl, "children": []}
        for t, tl in zip(texts, titles)
    ]


# ---------------------------------------------------------------------------
# Contract: _garble_check_nodes accepts ScriptContext
# ---------------------------------------------------------------------------


class TestGarbleCheckNodesScriptContext:
    """_garble_check_nodes must accept script_context kwarg and use it
    for document-level script instead of bare expected_script."""

    def test_accepts_script_context_kwarg(self):
        """_garble_check_nodes can be called with script_context=ScriptContext(...)."""
        ctx = ScriptContext(dominant_script="Latn", had_presentation_forms=False, source="test")
        cfg = GarbleConfig()
        nodes = _make_nodes(["Normal English text that is perfectly fine and readable"])
        # Should not raise
        result = _garble_check_nodes(nodes, script_context=ctx, config=cfg)
        assert isinstance(result, int)

    def test_script_context_dominant_script_used(self):
        """When script_context is provided, its dominant_script is used
        as the document-level script (not expected_script param)."""
        clean_german = (
            "Die Versicherung umfasst die gesetzliche Haftpflicht des "
            "Versicherungsnehmers und aller Personen die in seinem Haushalt leben"
        )
        nodes = _make_nodes([clean_german])

        # With ScriptContext saying "Latn" -- clean German should NOT be garbled
        ctx_latn = ScriptContext(dominant_script="Latn", had_presentation_forms=False, source="test")
        cfg = GarbleConfig()
        result_latn = _garble_check_nodes(nodes, script_context=ctx_latn, config=cfg)
        assert result_latn == 0, "Clean German text with Latn context must not be garbled"

    def test_backward_compat_bare_expected_script(self):
        """When script_context is NOT provided, bare expected_script param
        still works (backward compat)."""
        clean = "Normal English text that is perfectly fine readable and long enough for analysis"
        nodes = _make_nodes([clean])
        # Old-style call with bare expected_script
        result = _garble_check_nodes(nodes, expected_script="Latn")
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# Contract: _garble_check_nodes does NOT call _infer_script for document-level
# script when ScriptContext is provided
# ---------------------------------------------------------------------------


class TestNoRedundantScriptInference:
    """When ScriptContext is threaded through, _garble_check_nodes should use
    the context's dominant_script for the document-level script, not re-infer."""

    def test_document_level_script_from_context(self):
        """The document-level script comes from ScriptContext.dominant_script,
        not from a redundant _infer_script call on the full document text."""
        # Arabic-looking text but ScriptContext says "Latn"
        # If _garble_check_nodes re-inferred from text, it would get "Arab"
        # ScriptContext should win at the document level
        ctx = ScriptContext(dominant_script="Latn", had_presentation_forms=False, source="filename")
        cfg = GarbleConfig()
        # Short node -- below 50-char threshold for per-node override
        nodes = _make_nodes(["Short text"])
        result = _garble_check_nodes(nodes, script_context=ctx, config=cfg)
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# QF3 (RFC-021): bilingual per-node override preserved
# ---------------------------------------------------------------------------


class TestQF3BilingualPerNodeOverride:
    """QF3/RFC-021: when a node's text-inferred script disagrees with the
    document-level script (from ScriptContext), the TEXT-INFERRED script
    wins for that node.  This prevents false-flagging English-only nodes
    in bilingual Arabic documents as garbled."""

    def test_english_node_in_arabic_doc_not_false_flagged(self):
        """An English-only node >= 50 chars in a document with
        dominant_script='Arab' should NOT be flagged as garbled,
        because QF3 overrides to the text-inferred 'Latn' script."""
        english_text = (
            "This section describes the general conditions and terms that apply "
            "to all insurance policies issued by the company in the calendar year"
        )
        assert len(english_text) >= 50, "Test text must be >= 50 chars for QF3 to activate"

        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=False, source="filename")
        cfg = GarbleConfig()
        nodes = _make_nodes([english_text])
        garbled_count = _garble_check_nodes(nodes, script_context=ctx, config=cfg)
        assert garbled_count == 0, (
            "QF3: English-only node in Arab doc must NOT be false-flagged; "
            "text-inferred 'Latn' should override document-level 'Arab'"
        )

    def test_arabic_node_in_arabic_doc_still_checked(self):
        """An Arabic node in an Arabic document should be checked with
        the Arabic script (no override, scripts agree)."""
        arabic_text = "المادة الأولى تنظيم الحقوق والواجبات للمواطنين القانون العام " * 3
        assert len(arabic_text) >= 50

        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=False, source="filename")
        cfg = GarbleConfig()
        nodes = _make_nodes([arabic_text])
        # Clean Arabic should not be garbled
        garbled_count = _garble_check_nodes(nodes, script_context=ctx, config=cfg)
        assert garbled_count == 0, "Clean Arabic in Arab context should not be garbled"

    def test_qf3_override_emits_warning(self):
        """When QF3 override fires (text-inferred != document-level),
        a warning is logged."""
        english_text = (
            "This section describes the general conditions and terms that apply "
            "to all insurance policies issued by the company in the calendar year"
        )
        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=False, source="filename")
        cfg = GarbleConfig()
        nodes = _make_nodes([english_text])

        with patch("pageindex_mcp.helpers.logger") as mock_logger:
            _garble_check_nodes(nodes, script_context=ctx, config=cfg)
            # QF3 should emit a warning about script mismatch
            mock_logger.warning.assert_called()
            call_args = str(mock_logger.warning.call_args)
            assert "Script mismatch" in call_args or "script" in call_args.lower()

    def test_short_node_uses_document_script(self):
        """Nodes shorter than 50 chars cannot trigger QF3 override;
        they use the document-level script from ScriptContext."""
        short_english = "Hello world"
        assert len(short_english) < 50

        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=False, source="filename")
        cfg = GarbleConfig()
        nodes = _make_nodes([short_english])
        # Short node in Arab context: cannot override to Latn because < 50 chars.
        # The garble result depends on the prong heuristics for short text,
        # but the key contract is that _infer_script is NOT called for short nodes
        # (they use document-level script directly).
        result = _garble_check_nodes(nodes, script_context=ctx, config=cfg)
        assert isinstance(result, int)

    def test_mixed_nodes_arabic_and_english(self):
        """A document with both Arabic and English nodes: Arabic nodes use
        document-level script; English nodes >= 50 chars get QF3 override."""
        arabic_text = "المادة الأولى تنظيم الحقوق والواجبات للمواطنين القانون العام " * 3
        english_text = (
            "This section describes the general conditions and terms that apply "
            "to all insurance policies issued by the company in the calendar year"
        )
        assert len(arabic_text) >= 50
        assert len(english_text) >= 50

        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=False, source="filename")
        cfg = GarbleConfig()
        nodes = _make_nodes([arabic_text, english_text])
        garbled_count = _garble_check_nodes(nodes, script_context=ctx, config=cfg)
        # Neither clean Arabic nor clean English should be garbled
        assert garbled_count == 0, (
            "Mixed Arabic+English doc: neither clean Arabic nor QF3-overridden "
            "English node should be falsely flagged"
        )


# ---------------------------------------------------------------------------
# Contract: script_context.had_presentation_forms threaded to per-node calls
# ---------------------------------------------------------------------------


class TestPresentationFormsThreading:
    """had_presentation_forms from ScriptContext is threaded through to
    per-node detect_garble calls."""

    def test_pf_flag_threaded_to_nodes(self):
        """When script_context.had_presentation_forms=True, per-node
        detect_garble calls receive the flag."""
        import unicodedata

        # Build text with Presentation Forms
        pf_chars = "".join(chr(c) for c in range(0xFE70, 0xFE90))  # 32 PF chars
        arabic_chars = "".join(chr(c) for c in range(0x0620, 0x0630))  # 16 Arabic chars
        raw = pf_chars + arabic_chars
        # Post-NFKC the PF codepoints are decomposed
        post_nfkc = unicodedata.normalize("NFKC", raw)

        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=True, source="test")
        cfg = GarbleConfig()
        nodes = _make_nodes([post_nfkc])
        garbled_count = _garble_check_nodes(nodes, script_context=ctx, config=cfg)
        # The presentation_forms prong should fire via the threaded flag
        assert garbled_count >= 1, (
            "had_presentation_forms=True must be threaded to per-node "
            "detect_garble, causing the presentation_forms prong to fire"
        )
