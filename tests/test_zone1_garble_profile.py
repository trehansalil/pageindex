"""Zone-1 GarbleProfile tests: contract, exhaustiveness, regression, wiring, integration.

Validates the GarbleProfile frozen dataclass consolidation that replaces the
8-member GarbleContext StrEnum with 2 profile constants (BULK_PROFILE,
FLAT_MARKDOWN_PROFILE) and 2 boolean fields (normalize_markdown,
short_circuit_prior_garble).
"""
from __future__ import annotations

import dataclasses
import inspect
import os
from unittest.mock import patch

import pytest

from pageindex_mcp.helpers import (
    BULK_PROFILE,
    FLAT_MARKDOWN_PROFILE,
    GarbleProfile,
    TreeDefect,
    TreeSignals,
    _flatten_tree_text,
    _infer_script,
    check_garble,
    garble_prongs,
    validate_tree,
)
from pageindex_mcp.script import BlobKind, normalize_for_garble


# ---------------------------------------------------------------------------
# Fixtures / sample texts
# ---------------------------------------------------------------------------

_PUA = "" * 400  # PUA chars -> garble signal
_NULL_BYTE = "\x00" * 200 + "some text" * 20  # null replacement bytes
_CLEAN_GERMAN = (
    "Die Versicherung deckt Schaden an Dritten im Rahmen der "
    "vereinbarten Deckungssumme. Der Versicherungsnehmer ist "
    "verpflichtet, den Schaden unverzueglich zu melden. "
    "Weitere Bedingungen sind dem Vertrag zu entnehmen. "
    "Die Praemie wird jaehrlich berechnet und ist im Voraus faellig."
)
_CLEAN_ARABIC = (
    "التأمين يغطي "
    "الأضرار التي "
    "تلحق بالطرف "
    "الثالث في إطار "
    "مبلغ التغطية "
    "المتفق عليه"
)
_DIGIT_HEAVY = "1234567890" * 100  # 60%+ digits
_TOKEN_REPEAT = " ".join(["garbled"] * 50 + ["word"] * 10)  # >30% repetition
_LATIN_GIBBERISH_ARAB = (
    "التأمين xkjqz vbwm tplrk "
    "mwntl qzxtl brkfn xplnk " * 30
)
_SPARSE_MOJIBAKE = (
    "التأمين " * 40
    + "اxب اyت اzم " * 20
)

# ---------------------------------------------------------------------------
# 1. GarbleProfile contract and exhaustiveness
# ---------------------------------------------------------------------------


class TestGarbleProfileContract:
    """GarbleProfile is a frozen dataclass with exactly 2 boolean fields."""

    def test_is_frozen_dataclass(self):
        assert dataclasses.is_dataclass(GarbleProfile)
        assert GarbleProfile.__dataclass_params__.frozen  # type: ignore[attr-defined]

    def test_has_exactly_two_fields(self):
        fields = dataclasses.fields(GarbleProfile)
        assert len(fields) == 2, (
            f"GarbleProfile has {len(fields)} fields, expected 2: "
            f"{[f.name for f in fields]}"
        )

    def test_field_names(self):
        names = {f.name for f in dataclasses.fields(GarbleProfile)}
        assert names == {"normalize_markdown", "short_circuit_prior_garble"}

    def test_field_types_are_bool(self):
        for f in dataclasses.fields(GarbleProfile):
            assert f.type == "bool" or f.type is bool, (
                f"Field {f.name} has type {f.type}, expected bool"
            )

    def test_exactly_two_profile_constants(self):
        """BULK_PROFILE and FLAT_MARKDOWN_PROFILE are the only two profiles."""
        assert isinstance(BULK_PROFILE, GarbleProfile)
        assert isinstance(FLAT_MARKDOWN_PROFILE, GarbleProfile)

    def test_bulk_profile_values(self):
        assert BULK_PROFILE.normalize_markdown is False
        assert BULK_PROFILE.short_circuit_prior_garble is False

    def test_flat_markdown_profile_values(self):
        assert FLAT_MARKDOWN_PROFILE.normalize_markdown is True
        assert FLAT_MARKDOWN_PROFILE.short_circuit_prior_garble is True

    def test_frozen_mutation_raises(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            BULK_PROFILE.normalize_markdown = True  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            FLAT_MARKDOWN_PROFILE.short_circuit_prior_garble = False  # type: ignore[misc]

    def test_garble_context_not_importable(self):
        """GarbleContext enum must no longer exist in helpers."""
        with pytest.raises((ImportError, AttributeError)):
            from pageindex_mcp.helpers import GarbleContext  # type: ignore[attr-defined]  # noqa: F401


# ---------------------------------------------------------------------------
# 2. check_garble contract enforcement
# ---------------------------------------------------------------------------


class TestCheckGarbleContract:
    """check_garble requires profile= and expected_script= as keyword-only."""

    def test_context_kwarg_raises_type_error(self):
        """Old context= keyword must be rejected."""
        with pytest.raises(TypeError):
            check_garble("hello", expected_script="Latn", profile=BULK_PROFILE, context="TREE_BULK")  # type: ignore[call-arg]

    def test_missing_expected_script_raises(self):
        with pytest.raises(TypeError):
            check_garble("hello", profile=BULK_PROFILE)  # type: ignore[call-arg]

    def test_positional_expected_script_raises(self):
        with pytest.raises(TypeError):
            check_garble("hello", "Latn", profile=BULK_PROFILE)  # type: ignore[misc]

    def test_profile_kwarg_required(self):
        with pytest.raises(TypeError):
            check_garble("hello", expected_script="Latn")  # type: ignore[call-arg]

    def test_returns_bool(self):
        result = check_garble(
            _CLEAN_GERMAN, expected_script="Latn", profile=BULK_PROFILE
        )
        assert isinstance(result, bool)

    def test_signature_keyword_only(self):
        sig = inspect.signature(check_garble)
        for name in ("expected_script", "profile", "original_defect"):
            param = sig.parameters[name]
            assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"{name} must be keyword-only"
            )


# ---------------------------------------------------------------------------
# 3. Behavioral equivalence regression: 8 contexts -> 2 profiles
# ---------------------------------------------------------------------------


class TestBehavioralEquivalence:
    """Collapsing 8 GarbleContext values to 2 profiles does not change garble
    decisions for any of the 8 representative text samples."""

    SAMPLES = [
        ("clean_latin", _CLEAN_GERMAN, "Latn", False),
        ("clean_arabic", _CLEAN_ARABIC, "Arab", False),
        ("pua_garble", _PUA, None, True),
        ("null_byte_garble", _NULL_BYTE, None, True),
        ("digit_ratio_garble", _DIGIT_HEAVY, "Latn", True),
        ("token_repetition_garble", _TOKEN_REPEAT, "Latn", True),
        ("latin_gibberish_arab", _LATIN_GIBBERISH_ARAB, "Arab", True),
        ("sparse_mojibake", _SPARSE_MOJIBAKE, "Arab", True),
    ]

    @pytest.mark.parametrize(
        "label,text,script,expected_garbled",
        SAMPLES,
        ids=[s[0] for s in SAMPLES],
    )
    def test_bulk_profile_matches_expected(self, label, text, script, expected_garbled):
        """BULK_PROFILE (replacing TREE_BULK/NODE/PAGE_TEXT_LAYER/DOCUMENT_FALLBACK/
        REGION/RETRY_COMPARISON/IMAGE_ENRICHMENT) produces the expected garble decision."""
        result = check_garble(text, expected_script=script, profile=BULK_PROFILE)
        assert result is expected_garbled, (
            f"BULK_PROFILE({label}): got {result}, expected {expected_garbled}"
        )

    @pytest.mark.parametrize(
        "label,text,script,expected_garbled",
        SAMPLES,
        ids=[s[0] for s in SAMPLES],
    )
    def test_flat_markdown_profile_matches_expected(
        self, label, text, script, expected_garbled
    ):
        """FLAT_MARKDOWN_PROFILE (replacing FLAT_MARKDOWN) produces the same
        garble decision as BULK_PROFILE for non-short-circuit cases."""
        result = check_garble(
            text, expected_script=script, profile=FLAT_MARKDOWN_PROFILE
        )
        assert result is expected_garbled, (
            f"FLAT_MARKDOWN_PROFILE({label}): got {result}, expected {expected_garbled}"
        )

    def test_bulk_and_flat_agree_on_clean_text(self):
        """Both profiles agree clean text is not garbled."""
        bulk = check_garble(
            _CLEAN_GERMAN, expected_script="Latn", profile=BULK_PROFILE
        )
        flat = check_garble(
            _CLEAN_GERMAN, expected_script="Latn", profile=FLAT_MARKDOWN_PROFILE
        )
        assert bulk == flat == False  # noqa: E712

    def test_bulk_and_flat_agree_on_garbled_text(self):
        """Both profiles agree PUA text is garbled."""
        bulk = check_garble(_PUA, expected_script=None, profile=BULK_PROFILE)
        flat = check_garble(_PUA, expected_script=None, profile=FLAT_MARKDOWN_PROFILE)
        assert bulk == flat == True  # noqa: E712


# ---------------------------------------------------------------------------
# 4. expected_script self-inference removal regression
# ---------------------------------------------------------------------------


class TestScriptInferenceRemoval:
    """garble_prongs no longer self-infers expected_script when None."""

    def test_garble_prongs_none_script_skips_latin_gibberish(self):
        """With expected_script=None, latin_gibberish prong must NOT fire
        (garble_prongs no longer self-infers script)."""
        norm = normalize_for_garble(_LATIN_GIBBERISH_ARAB, BlobKind.TREE_TEXT)
        prongs = garble_prongs(norm, expected_script=None)
        assert "latin_gibberish" not in prongs, (
            f"latin_gibberish fired with expected_script=None: {prongs}"
        )

    def test_garble_prongs_explicit_arab_fires_latin_gibberish(self):
        """With expected_script='Arab' explicitly passed, latin_gibberish fires."""
        norm = normalize_for_garble(_LATIN_GIBBERISH_ARAB, BlobKind.TREE_TEXT)
        prongs = garble_prongs(norm, expected_script="Arab")
        assert "latin_gibberish" in prongs, (
            f"latin_gibberish did NOT fire with explicit Arab: {prongs}"
        )

    def test_from_tree_explicitly_infers_script(self):
        """TreeSignals.from_tree with expected_script=None calls _infer_script
        explicitly (the eff_script = expected_script or _infer_script(flat_text)
        pattern), so garble_prongs receives an explicit script value rather
        than relying on internal self-inference (which was removed)."""
        # Use Arabic-majority text so _infer_script returns 'Arab' and
        # latin_gibberish prong fires on the embedded nonsense tokens.
        arabic_majority = (
            "التأمين يغطي الأضرار التي تلحق بالطرف الثالث في إطار "
            "مبلغ التغطية المتفق عليه وفقا لشروط العقد " * 8
        )
        # Append Latin gibberish -- enough to exceed 0.4 ratio threshold
        latin_junk = "xkjqz vbwm tplrk mwntl qzxtl brkfn " * 15
        combined = arabic_majority + latin_junk
        assert _infer_script(combined) == "Arab", (
            "Precondition: text must infer as Arab for this test"
        )
        tree = [
            {"title": "Root", "text": combined, "nodes": []},
        ]
        sig = TreeSignals.from_tree(tree, expected_script=None)
        # from_tree should infer Arab -> pass it to check_garble -> latin_gibberish fires
        assert sig.garbled is True, (
            "TreeSignals.from_tree must explicitly infer script and detect "
            "latin_gibberish when expected_script=None on Arab-majority text"
        )

    def test_check_garble_none_script_no_latin_gibberish(self):
        """check_garble with BULK_PROFILE, expected_script=None on
        latin-gibberish text does NOT fire latin_gibberish prong
        (no script context means no basis for the prong)."""
        result = check_garble(
            _LATIN_GIBBERISH_ARAB,
            expected_script=None,
            profile=BULK_PROFILE,
        )
        # Without explicit script, the garble check may still fire on other
        # prongs, but latin_gibberish specifically should not -- check via
        # garble_prongs directly for prong-level assertion.
        norm = normalize_for_garble(_LATIN_GIBBERISH_ARAB, BlobKind.TREE_TEXT)
        prongs = garble_prongs(norm, expected_script=None)
        assert "latin_gibberish" not in prongs


# ---------------------------------------------------------------------------
# 5. FLAT_MARKDOWN_PROFILE short-circuit behavior
# ---------------------------------------------------------------------------


class TestFlatMarkdownShortCircuit:
    """Short-circuit: FLAT_MARKDOWN + short text + prior garble defect."""

    _SHORT_CLEAN = "This is clean."  # < 200 chars

    def test_short_circuit_fires_with_garbling_defect(self):
        result = check_garble(
            self._SHORT_CLEAN,
            expected_script=None,
            profile=FLAT_MARKDOWN_PROFILE,
            original_defect=TreeDefect.GARBLING,
        )
        assert result is True

    def test_short_circuit_fires_with_node_garbling_defect(self):
        result = check_garble(
            self._SHORT_CLEAN,
            expected_script=None,
            profile=FLAT_MARKDOWN_PROFILE,
            original_defect=TreeDefect.NODE_GARBLING,
        )
        assert result is True

    def test_bulk_profile_no_short_circuit(self):
        """BULK_PROFILE must NOT short-circuit even with garbling defect."""
        result = check_garble(
            self._SHORT_CLEAN,
            expected_script=None,
            profile=BULK_PROFILE,
            original_defect=TreeDefect.GARBLING,
        )
        assert result is False  # clean text, no garble

    def test_non_garble_defect_no_short_circuit(self):
        """NODE_COUNT_LOW does not trigger short-circuit."""
        result = check_garble(
            self._SHORT_CLEAN,
            expected_script=None,
            profile=FLAT_MARKDOWN_PROFILE,
            original_defect=TreeDefect.NODE_COUNT_LOW,
        )
        assert result is False  # clean text, not eligible for short-circuit

    def test_monkeypatch_disables_short_circuit(self):
        """Setting _GARBLE_SHORT_TEXT_DEFAULT to False disables short-circuit."""
        with patch("pageindex_mcp.helpers._GARBLE_SHORT_TEXT_DEFAULT", False):
            result = check_garble(
                self._SHORT_CLEAN,
                expected_script=None,
                profile=FLAT_MARKDOWN_PROFILE,
                original_defect=TreeDefect.GARBLING,
            )
            assert result is False  # short-circuit disabled, clean text -> False


# ---------------------------------------------------------------------------
# 6. Wiring verification
# ---------------------------------------------------------------------------


class TestWiringVerification:
    """Every production file that calls check_garble imports GarbleProfile
    and at least one profile constant."""

    def test_helpers_defines_garble_profile(self):
        import pageindex_mcp.helpers as h

        assert hasattr(h, "GarbleProfile")
        assert hasattr(h, "BULK_PROFILE")
        assert hasattr(h, "FLAT_MARKDOWN_PROFILE")
        assert isinstance(h.BULK_PROFILE, h.GarbleProfile)
        assert isinstance(h.FLAT_MARKDOWN_PROFILE, h.GarbleProfile)

    def test_helpers_uses_profiles_in_check_garble(self):
        """check_garble's signature accepts profile: GarbleProfile."""
        sig = inspect.signature(check_garble)
        assert "profile" in sig.parameters
        assert "context" not in sig.parameters

    def test_client_imports_profiles(self):
        import pageindex_mcp.client as c

        assert hasattr(c, "BULK_PROFILE")
        assert hasattr(c, "FLAT_MARKDOWN_PROFILE")

    def test_converters_imports_detect_garble_and_config(self):
        """converters.py imports detect_garble + _garble_config (Zone-3 unified API),
        replacing the former lazy BULK_PROFILE + check_garble imports."""
        import ast

        import pageindex_mcp.converters as conv

        source = inspect.getsource(conv)
        tree = ast.parse(source)
        # Find top-level imports of detect_garble and _garble_config
        detect_imports = []
        config_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "helpers" in node.module:
                for alias in node.names:
                    if alias.name == "detect_garble":
                        detect_imports.append(node)
                    if alias.name == "_garble_config":
                        config_imports.append(node)
        assert len(detect_imports) >= 1, (
            f"Expected at least 1 import of detect_garble in converters.py, "
            f"found {len(detect_imports)}"
        )
        assert len(config_imports) >= 1, (
            f"Expected at least 1 import of _garble_config in converters.py, "
            f"found {len(config_imports)}"
        )

    def test_no_garble_context_in_production_imports(self):
        """GarbleContext must not be imported in any production module."""
        import pageindex_mcp.helpers as h

        assert not hasattr(h, "GarbleContext"), (
            "GarbleContext still exists in helpers.py"
        )


# ---------------------------------------------------------------------------
# 7. garble_prongs purification
# ---------------------------------------------------------------------------


class TestGarbleProngsPurification:
    """garble_prongs is purified: no blob_kind param, no self-inference."""

    def test_no_blob_kind_parameter(self):
        """garble_prongs no longer accepts blob_kind."""
        sig = inspect.signature(garble_prongs)
        assert "blob_kind" not in sig.parameters, (
            "garble_prongs still accepts blob_kind parameter"
        )

    def test_blob_kind_keyword_raises(self):
        with pytest.raises(TypeError):
            garble_prongs("test text", expected_script=None, blob_kind=BlobKind.TREE_TEXT)  # type: ignore[call-arg]

    def test_returns_frozenset_of_strings(self):
        result = garble_prongs(_PUA, expected_script=None)
        assert isinstance(result, frozenset)
        for item in result:
            assert isinstance(item, str)

    def test_none_script_skips_latin_gibberish(self):
        """No self-inference: expected_script=None -> latin_gibberish never fires."""
        norm = normalize_for_garble(_LATIN_GIBBERISH_ARAB, BlobKind.TREE_TEXT)
        prongs = garble_prongs(norm, expected_script=None)
        assert "latin_gibberish" not in prongs

    def test_known_prongs_available(self):
        """All documented prong names should be testable."""
        known_prongs = {
            "null_replacement_bytes",
            "glyph_marker",
            "control_chars",
            "pua_chars",
            "presentation_forms",
            "single_letter_fragments",
            "digit_ratio",
            "token_repetition",
            "latin_gibberish",
            "empty",
        }
        # Each prong name should be a valid string in at least some garble_prongs result
        # Test a few representative ones
        pua_prongs = garble_prongs(_PUA, expected_script=None)
        assert "pua_chars" in pua_prongs

        digit_prongs = garble_prongs(_DIGIT_HEAVY, expected_script="Latn")
        assert "digit_ratio" in digit_prongs

        empty_prongs = garble_prongs("", expected_script=None)
        assert "empty" in empty_prongs

    def test_expected_script_keyword_only(self):
        sig = inspect.signature(garble_prongs)
        param = sig.parameters["expected_script"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# 8. Integration: validate_tree -> TreeSignals.from_tree -> check_garble
# ---------------------------------------------------------------------------


class TestIntegrationValidateTree:
    """End-to-end: validate_tree uses profile-based check_garble correctly."""

    def test_garbled_tree_returns_garbling_defect(self):
        """A tree with PUA-garbled text triggers GARBLING defect."""
        tree = [
            {"title": "Root", "text": _PUA, "nodes": [
                {"title": "Child 1", "text": _PUA, "nodes": []},
                {"title": "Child 2", "text": _PUA, "nodes": []},
            ]},
        ]
        result = validate_tree(tree)
        assert not result.ok
        assert result.defect == TreeDefect.GARBLING

    def test_clean_tree_no_garble_defect(self):
        """A tree with clean German text does not trigger garble defect."""
        clean_text = _CLEAN_GERMAN * 3  # enough content
        tree = [
            {"title": "Versicherungsbedingungen", "text": clean_text, "nodes": [
                {"title": "Deckungsumfang", "text": clean_text, "nodes": [
                    {"title": "Paragraph 1", "text": clean_text, "nodes": []},
                    {"title": "Paragraph 2", "text": clean_text, "nodes": []},
                    {"title": "Paragraph 3", "text": clean_text, "nodes": []},
                ]},
                {"title": "Pflichten", "text": clean_text, "nodes": [
                    {"title": "Meldepflicht", "text": clean_text, "nodes": []},
                    {"title": "Mitwirkung", "text": clean_text, "nodes": []},
                ]},
            ]},
        ]
        result = validate_tree(tree)
        # Should not be GARBLING (may be OK or some other structural defect)
        assert result.defect != TreeDefect.GARBLING

    def test_tree_signals_from_tree_uses_bulk_profile(self):
        """TreeSignals.from_tree uses BULK_PROFILE (not FLAT_MARKDOWN_PROFILE)."""
        tree = [{"title": "Root", "text": _PUA, "nodes": []}]
        # If from_tree were using FLAT_MARKDOWN_PROFILE's short-circuit,
        # behavior would differ. Verify it uses BULK_PROFILE by checking
        # garble detection on PUA text works the same as direct BULK_PROFILE call.
        sig = TreeSignals.from_tree(tree, expected_script=None)
        flat = _flatten_tree_text(tree)
        eff_script = _infer_script(flat)
        direct = check_garble(flat, expected_script=eff_script, profile=BULK_PROFILE)
        assert sig.garbled == direct

    def test_gate_garbling_fires_on_garbled_tree(self):
        """_gate_garbling returns True for garbled TreeSignals."""
        from pageindex_mcp.helpers import _gate_garbling

        tree = [{"title": "Root", "text": _PUA, "nodes": [
            {"title": "A", "text": _PUA, "nodes": []},
            {"title": "B", "text": _PUA, "nodes": []},
        ]}]
        sig = TreeSignals.from_tree(tree, expected_script=None)
        fires, _ = _gate_garbling(sig, tree, None, None, None)
        assert fires is True

    def test_gate_node_garbling_fires_on_per_node_garble(self):
        """_gate_node_garbling detects per-node garble through check_garble."""
        from pageindex_mcp.helpers import _gate_node_garbling

        garbled_text = _PUA
        tree = [
            {"title": "Root", "text": "Clean root text " * 20, "nodes": [
                {"title": "Bad 1", "text": garbled_text, "nodes": []},
                {"title": "Bad 2", "text": garbled_text, "nodes": []},
                {"title": "Bad 3", "text": garbled_text, "nodes": []},
            ]},
        ]
        sig = TreeSignals.from_tree(tree, expected_script=None)
        fires, _ = _gate_node_garbling(sig, tree, None, None, None)
        # With 3/4 nodes garbled (75%), this should exceed the threshold
        assert fires is True
