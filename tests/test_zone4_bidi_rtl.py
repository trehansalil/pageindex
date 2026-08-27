"""Zone 4 — Bidi/RTL Processing Split contract tests.

Tests the wave-2 fixes:
- pictures._pre_inference_normalize delegates to normalize canonical impl
- _gate_bidi_degraded fires on had_presentation_forms
- BIDI_NORM_VERSION exported from converters
"""

import pytest

from pageindex_mcp.converters.normalize import BIDI_NORM_VERSION
from pageindex_mcp.helpers.gates import _gate_bidi_degraded
from pageindex_mcp.script import RtlDecision, ScriptContext


class TestBidiNormVersion:

    def test_version_is_int(self):
        assert isinstance(BIDI_NORM_VERSION, int)
        assert BIDI_NORM_VERSION >= 2

    def test_exported_from_converters_init(self):
        from pageindex_mcp.converters import BIDI_NORM_VERSION as exported
        assert exported == BIDI_NORM_VERSION


class TestPreInferenceNormalizeDelegation:

    def test_arabic_presentation_forms_detected(self):
        from pageindex_mcp.converters.pictures import _pre_inference_normalize

        text = "ﭐﭑﭒ مرحبا بالعالم"  # U+FB50-range chars + regular Arabic
        _, rtl_decision = _pre_inference_normalize(text)
        assert rtl_decision is not None
        assert rtl_decision.had_presentation_forms is True

    def test_latin_text_works(self):
        from pageindex_mcp.converters.pictures import _pre_inference_normalize

        text = "# Hello World\n\nSome plain English text."
        result_text, _ = _pre_inference_normalize(text)
        assert isinstance(result_text, str)

    def test_matches_canonical_implementation(self):
        from pageindex_mcp.converters.normalize import _pre_inference_normalize as canonical
        from pageindex_mcp.converters.pictures import _pre_inference_normalize as pictures_impl

        text = "## Section\n\nNormal text with no special chars."
        assert pictures_impl(text) == canonical(text)


class TestGateBidiDegradedPresentationForms:

    @pytest.fixture()
    def dummy_sig(self):
        from pageindex_mcp.helpers.tree_validation import TreeSignals
        return TreeSignals(
            node_count=5,
            depth=3,
            max_leaf_ratio=0.4,
            flat_text="dummy text",
            garbled=False,
            garble_ratio=0.0,
            effectively_garbled=False,
            is_reordered=False,
            expected_min_depth=2,
        )

    @pytest.fixture()
    def dummy_script_ctx(self):
        return ScriptContext(
            dominant_script="ar",
            had_presentation_forms=False,
            source="test",
        )

    def test_fires_on_had_presentation_forms(self, dummy_sig, dummy_script_ctx):
        rtl = RtlDecision(
            reversed=False,
            repair_effective=False,
            sampled=0,
            method="test",
            had_presentation_forms=True,
        )
        fires, detail = _gate_bidi_degraded(
            dummy_sig, [], dummy_script_ctx, None, rtl
        )
        assert fires is True
        assert "had_presentation_forms=True" in detail

    def test_fires_on_reversed(self, dummy_sig, dummy_script_ctx):
        rtl = RtlDecision(
            reversed=True,
            repair_effective=False,
            sampled=0,
            method="test",
            had_presentation_forms=False,
        )
        fires, detail = _gate_bidi_degraded(
            dummy_sig, [], dummy_script_ctx, None, rtl
        )
        assert fires is True
        assert "reversed=True" in detail

    def test_does_not_fire_when_both_false(self, dummy_sig, dummy_script_ctx):
        rtl = RtlDecision(
            reversed=False,
            repair_effective=False,
            sampled=0,
            method="test",
            had_presentation_forms=False,
        )
        fires, _ = _gate_bidi_degraded(
            dummy_sig, [], dummy_script_ctx, None, rtl
        )
        assert fires is False

    def test_does_not_fire_when_rtl_none(self, dummy_sig, dummy_script_ctx):
        fires, _ = _gate_bidi_degraded(
            dummy_sig, [], dummy_script_ctx, None, None
        )
        assert fires is False

    def test_disabled_by_env(self, dummy_sig, dummy_script_ctx, monkeypatch):
        monkeypatch.setenv("BIDI_COHERENCE_ENFORCE", "false")
        rtl = RtlDecision(
            reversed=True,
            repair_effective=False,
            sampled=0,
            method="test",
            had_presentation_forms=True,
        )
        fires, _ = _gate_bidi_degraded(
            dummy_sig, [], dummy_script_ctx, None, rtl
        )
        assert fires is False
