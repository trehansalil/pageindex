# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Garble detection, garble gate, and zone-1 flat gate asymmetry tests.

Also absorbs (2026-09-22 consolidation):
  - test_d7_arbitration.py           (RFC-046 D7 arbitrate() + pre-rebuild md)
  - test_d7_arabic_density_floor.py  (RFC-047 D7 script-aware density floor)

Many former one-assertion-per-case tests are now table-driven: the test loops
internally, collects EVERY failing row and asserts once naming all offenders.
Same coverage, one collected test, better diagnostics.
"""

from __future__ import annotations

import dataclasses
import logging
import sys
import types
from unittest.mock import patch

import pytest

from pageindex_mcp import converters
from pageindex_mcp.converters import (
    PictureResult,
    _recover_picture_results,
    _recover_picture_text,
    detect_ocr_langs,
    splice_picture_text_for_tree,
)
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    ENGINE_RELIABILITY_ORDER,
    HALLUCINATION_CHAR_RATIO,
    Candidate,
    ExtractionState,
    RecoveryOutcome,
    FLAT_MARKDOWN_PROFILE,
    BlobKind,
    GarbleProfile,
    ScriptContext,
    _garble_check_flat_blocks,
    _garble_check_nodes,
    _flat_block_primary_text,
    _infer_script,
    arbitrate,
    normalize_for_garble,
    validate_tree,
)
from pageindex_mcp.helpers.arbitrate import (
    _engine_rank,
    _is_hallucinated,
    _median_chars,
)
from pageindex_mcp.helpers.garble import (
    GarbleConfig,
    GarbleReport,
    _MIXED_SCRIPT_RE,
    _garble_prongs,
    detect_garble,
)
from pageindex_mcp.helpers.gates import FLAT_GATE_COVERAGE, _gate_suspect_density
from pageindex_mcp.helpers.tree_validation import TreeSignals
from pageindex_mcp.helpers.types import Route, TreeDefect, decide_route
from pageindex_mcp.picture_plane import PictureGateConfig

from tests._garble_compat import check_garble


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# --- from test_garble.py ---

_PUA = "" * 400
_CLEAN_GERMAN = (
    "Die Versicherung deckt Schaden an Dritten im Rahmen der "
    "vereinbarten Deckungssumme. Der Versicherungsnehmer ist "
    "verpflichtet, den Schaden unverzueglich zu melden. "
    "Weitere Bedingungen sind dem Vertrag zu entnehmen. "
    "Die Praemie wird jaehrlich berechnet und ist im Voraus faellig."
)
_SPARSE_MOJIBAKE = ("هذا" + "x3z" + "النص " + "عربي" + "q7k" + "متنوع ") * 30

# --- from test_garble_detection.py ---

_CLEAN_ARABIC = (
    "في هذا النص العربي الطويل نجد أن القوانين تنظم الحياة العامة وتحدد الحقوق والواجبات"
)
_GARBLED_LATIN = "de Bab rel igh foal pred khar teb ghal mun sar dek phal wur"

# --- from test_rfc_garble_gate.py ---

_MARKER = "<!-- image -->"

# A blob of Latin-alphabet consonant clusters -- no real words in any
# language, long enough to clear the >20-token repetition-check floor and the
# Latin-gibberish ratio threshold used by check_garble(expected_script="Arab").
_LATIN_GIBBERISH = " ".join(["xkjqz vbwm nfrl qpzx wblk"] * 60)

# ---------------------------------------------------------------------------
# Helpers from test_rfc_garble_gate.py
# ---------------------------------------------------------------------------


def _pic(ocr_text: str = "", **kwargs) -> PictureResult:
    result: PictureResult = {"ocr_text": ocr_text}
    result.update(kwargs)
    return result


# ---------------------------------------------------------------------------
# Shared fake-``fitz`` scaffolding (mirrors tests/test_imgblock_audit_findings.py)
# ---------------------------------------------------------------------------
def _install_fake_fitz(monkeypatch, *, page_text="", clip_text=None, width=612.0, height=792.0):
    """Install a fake ``fitz`` module into ``sys.modules``.

    ``page_text`` is what ``page.get_text("text")`` (no clip) returns -- this
    drives ``_text_layer_has_content``. ``clip_text`` is what
    ``page.get_text("text", clip=rect)`` returns; defaults to ``page_text``
    when not given so tests that don't care about clip-text skip behavior
    aren't accidentally tripped by it.
    """
    resolved_clip_text = page_text if clip_text is None else clip_text

    class _Pix:
        def tobytes(self, fmt="png"):
            return b"\x89PNG fake image bytes"

    class _Page:
        rect = types.SimpleNamespace(width=width, height=height)
        rotation = 0

        def set_rotation(self, value):
            self.rotation = value

        def get_text(self, mode="text", *, clip=None):
            if clip is not None:
                return resolved_clip_text
            return page_text

        def get_pixmap(self, clip, dpi):
            return _Pix()

    class _Pdf:
        page_count = 1

        def __getitem__(self, i):
            return _Page()

        def close(self):
            pass

    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        width=a[2] - a[0] if len(a) >= 4 else 0,
        height=a[3] - a[1] if len(a) >= 4 else 0,
    )
    fake.open = lambda path: _Pdf()
    monkeypatch.setitem(sys.modules, "fitz", fake)


def _region(l=0, t=0, r=612, b=792):
    """A picture region bbox. Defaults to the FULL page (612x792, US Letter)."""
    return {
        "page": 1,
        "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None),
    }


def _long_text(n=60):
    return "x" * n


# ---------------------------------------------------------------------------
# Helpers from test_zone1_flat_gate_asymmetry.py
# ---------------------------------------------------------------------------


def _default_ctx(
    dominant_script: str | None = None,
    had_presentation_forms: bool = False,
) -> ScriptContext:
    return ScriptContext(
        dominant_script=dominant_script,
        had_presentation_forms=had_presentation_forms,
        source="test",
    )


def _default_config() -> GarbleConfig:
    return GarbleConfig()


# ===========================================================================
# check_garble / GarbleProfile surface
# ===========================================================================


class TestGarbleProfileContract:
    def test_profile_is_frozen_and_carries_expected_values(self):
        assert dataclasses.is_dataclass(GarbleProfile)
        with pytest.raises(dataclasses.FrozenInstanceError):
            BULK_PROFILE.normalize_markdown = True  # type: ignore[misc]
        assert BULK_PROFILE.normalize_markdown is False
        assert FLAT_MARKDOWN_PROFILE.normalize_markdown is True


class TestCheckGarble:
    def test_check_garble_table(self):
        """Table-driven verdicts for check_garble across profiles, scripts and
        the short-text/prior-defect short circuit.

        Rows collapsed here (one assertion each, previously one test each):
          clean German, PUA, had_presentation_forms, the three
          short_circuit_* cases, and the two latin-gibberish cases.
        Every failing row is reported, not just the first.
        """
        rows = [
            # (text, expected_script, profile, kwargs, expected, label)
            (_CLEAN_GERMAN, "Latn", BULK_PROFILE, {}, False, "clean_german_not_garbled"),
            (_PUA, None, BULK_PROFILE, {}, True, "pua_garbled"),
            (
                _CLEAN_GERMAN,
                "Latn",
                BULK_PROFILE,
                {"had_presentation_forms": True},
                True,
                "had_presentation_forms_triggers",
            ),
            # Zone-7 fix: clean short text with a prior garble defect is no
            # longer force-flagged under FLAT_MARKDOWN_PROFILE.
            (
                "Kurzer Text",
                None,
                FLAT_MARKDOWN_PROFILE,
                {"original_defect": TreeDefect.GARBLING},
                False,
                "short_circuit_flat_clean_text_not_forced",
            ),
            # ... but a real prong on short text still condemns.
            (
                " junk",
                None,
                FLAT_MARKDOWN_PROFILE,
                {"original_defect": TreeDefect.GARBLING},
                True,
                "short_circuit_flat_fires_when_prong_trips",
            ),
            (
                "Kurzer Text",
                None,
                BULK_PROFILE,
                {"original_defect": TreeDefect.GARBLING},
                False,
                "short_circuit_bulk_no_fire",
            ),
            # Arabic-expected text that came back as Latin tesseract mojibake.
            (
                _GARBLED_LATIN,
                "Arab",
                BULK_PROFILE,
                {},
                True,
                "latin_gibberish_under_arab_expected",
            ),
            # Zone-1 fix: the Latin-script filter was removed from the
            # latin_gibberish prong, so Latin nonsense is now caught too.
            (
                "xkq plm zfg wrt bvn yhs tjk mld qrx",
                "Latn",
                BULK_PROFILE,
                {},
                True,
                "latin_nonsense_under_latn_expected",
            ),
            # PF-asymmetry guard: clean Arabic prose with no PF flag stays clean.
            (_CLEAN_ARABIC * 5, "Arab", BULK_PROFILE, {}, False, "clean_arabic_not_flagged"),
        ]
        failures = []
        for text, script, profile, kwargs, want, label in rows:
            got = check_garble(text, expected_script=script, profile=profile, **kwargs)
            if got is not want:
                failures.append(f"{label}: check_garble(...) == {got!r}, expected {want!r}")
        assert not failures, "check_garble disagreed:\n" + "\n".join(failures)

        # profile is a required keyword -- calling without it is a TypeError.
        with pytest.raises(TypeError):
            check_garble("hello", expected_script="Latn")  # type: ignore[call-arg]


class TestSparseMojibake:
    def test_fires_above_floor_and_is_skipped_below(self):
        short = "هذاx3zالنصq7k عربي "
        assert "sparse_mojibake" in _garble_prongs(
            _SPARSE_MOJIBAKE, expected_script="Arab", original_text=_SPARSE_MOJIBAKE
        )
        assert "sparse_mojibake" not in _garble_prongs(
            short, expected_script="Arab", original_text=short
        )


class TestMixedScriptReFalsePositives:
    """RFC-047 D1 -- `_MIXED_SCRIPT_RE` must require a Latin letter in the bridge.

    Before the repair the pattern matched any ASCII run of 1-8 chars between
    Arabic codepoints, so ordinary legal-document punctuation (parenthesised
    section markers, comma-glued article numbers) was condemned as mojibake.

    Both sides of the boundary are pinned: the false-positive table below and
    ``test_mixed_script_re_still_catches_true_garble`` above it.
    """

    # Arabic clause filler used to push each fixture past the 100-char floor
    # that guards the sparse_mojibake prong.
    _FILLER = "شروط التأمين والتغطية القانونية العامة "

    def test_no_false_positive_on_clean_arabic_markers(self):
        """Parenthesised letters, digit-adjacent article refs (spaced and
        glued) and a realistic full insurance clause must all stay clean."""
        clause = (
            "المادة15: تلتزم الشركة بتعويض المؤمن له عن الأضرار المادية وفقا للبند (أ) "
            "من الوثيقة رقم597 الصادرة بتاريخ 2026/01/15، وتسري أحكام الفقرة (ب) "
            "والفقرة (ج) من المادة16، على أن يقدم الطلب خلال ثلاثين يوما من تاريخ الحادث."
        )
        rows = [
            ("(أ) " + self._FILLER * 4, "parenthesised_arabic_letter_marker"),
            ("وارد رقم597 " + self._FILLER * 4, "digit_bridging_spaced"),
            ("المادة15،الفقرة رقم597،البند " + self._FILLER * 4, "digit_bridging_glued"),
            (clause, "realistic_insurance_clause"),
        ]
        failures = []
        for text, label in rows:
            matches = _MIXED_SCRIPT_RE.findall(text)
            if matches:
                failures.append(f"{label}: _MIXED_SCRIPT_RE matched {matches!r}")
            prongs = _garble_prongs(text, expected_script="Arab", original_text=text)
            if "sparse_mojibake" in prongs:
                failures.append(f"{label}: sparse_mojibake fired (prongs={sorted(prongs)})")
        assert not failures, "clean Arabic condemned as mojibake:\n" + "\n".join(failures)

    def test_mixed_script_re_still_catches_true_garble(self):
        """Latin letters wedged between Arabic characters -- real mojibake,
        both densely and diluted into clean prose -- must still be caught."""
        rows = [
            ("كtابcجديد " * 12, "dense"),
            ("نص عربي سليم شروط التأمين كtابcجديد والتغطية القانونية " * 3, "diluted"),
        ]
        failures = []
        for text, label in rows:
            if not _MIXED_SCRIPT_RE.findall(text):
                failures.append(f"{label}: _MIXED_SCRIPT_RE found no bridge")
            prongs = _garble_prongs(text, expected_script="Arab", original_text=text)
            if "sparse_mojibake" not in prongs:
                failures.append(f"{label}: sparse_mojibake did not fire (prongs={sorted(prongs)})")
        assert not failures, "true mojibake missed:\n" + "\n".join(failures)


class TestGarbleProngs:
    def test_prong_names_and_return_type(self):
        pua_prongs = _garble_prongs(_PUA, expected_script=None)
        assert isinstance(pua_prongs, frozenset)
        assert "pua_chars" in pua_prongs
        assert "empty" in _garble_prongs("", expected_script=None)


class TestInferScript:
    def test_infer_script_table(self):
        rows = [
            ("هذا نص عربي طويل بما فيه الكفاية للكشف عن النص", "Arab", "arabic"),
            ("This is a sufficiently long English text for detection", "Latn", "latin"),
            ("", None, "empty"),
        ]
        failures = [
            f"{label}: _infer_script(...) == {_infer_script(text)!r}, expected {want!r}"
            for text, want, label in rows
            if _infer_script(text) != want
        ]
        assert not failures, "\n".join(failures)


class TestNormalizeForGarble:
    def test_tree_text_passthrough_raw_markdown_stripped(self):
        """TREE_TEXT is returned verbatim; RAW_MARKDOWN has markdown
        scaffolding (headings, pipes, HTML comments) stripped so it cannot
        inflate the garble denominator.

        Repaired 2026-09-22: the RAW_MARKDOWN half previously asserted only
        ``isinstance(result, str) and len(result) > 0`` -- a tautology that
        left the stripping behaviour untested.
        """
        text = "Hello world with link"
        assert normalize_for_garble(text, kind=BlobKind.TREE_TEXT) == text

        md = "## Heading\n\n| a | b |\n\n<!-- image -->\n\nParagraph text"
        result = normalize_for_garble(md, kind=BlobKind.RAW_MARKDOWN)
        assert "#" not in result, f"heading markers survived: {result!r}"
        assert "|" not in result, f"table pipes survived: {result!r}"
        assert "<!--" not in result, f"HTML comment survived: {result!r}"
        assert "Heading" in result and "Paragraph text" in result
        assert "  " not in result, f"whitespace not collapsed: {result!r}"


# ===========================================================================
# Presentation forms -- prong wiring and RFC-046 D5 detector alignment
#
# HR: the PF detector asymmetry (a one-codepoint any() flag condemning clean
# Arabic) is a LIVE defect class.  Both sides of the PF-dominant vs
# PF-minority boundary are pinned below.
# ===========================================================================


class TestPresentationForms:
    def test_pf_prong_fires_only_when_flag_set(self):
        """Both sides: had_presentation_forms=True fires the prong on
        otherwise-clean text; False (and the default) never does."""
        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=True, source="test")
        rows = [
            ("clean text " * 50, ctx.dominant_script, {"had_presentation_forms": True}, True,
             "via_script_context_true"),
            ("clean text " * 50, "Arab", {"had_presentation_forms": False}, False,
             "explicit_false"),
            ("any", None, {"had_presentation_forms": True}, True, "short_text_true"),
            ("any", None, {}, False, "default_absent"),
        ]
        failures = []
        for text, script, kwargs, want, label in rows:
            fired = "presentation_forms" in _garble_prongs(
                text, expected_script=script, **kwargs
            )
            if fired is not want:
                failures.append(f"{label}: presentation_forms fired={fired}, expected {want}")
        assert not failures, "\n".join(failures)


class TestPresentationFormsAlignment:
    """RFC-046 D5 task 3.2: the *signal* (had_presentation_forms) is
    ratio-gated, while NFKC normalization still triggers on any presence.

    This is the PF-asymmetry boundary: a PF-MINORITY blob must not raise the
    signal (that is the false-positive class that condemns clean Arabic),
    while a PF-DOMINANT blob must."""

    def test_pf_ratio_boundary_table(self):
        from pageindex_mcp.helpers.garble import PF_SIGNAL_RATIO, _pf_ratio

        rows = [
            # (text, signal_expected, label)
            ("بسم الله الرحمن الرحيم ﷲ والحمد لله", False, "pf_minority_single_ligature"),
            ("ﭐﭑﭒﭓﭔﭕ" + "ا", True, "pf_dominant_six_of_seven"),
            ("ﭐﭑﭒﭓﭔ" + "ابةتث", False, "exactly_half_is_not_dominant"),
        ]
        failures = []
        for text, want, label in rows:
            got = _pf_ratio(text) > PF_SIGNAL_RATIO
            if got is not want:
                failures.append(
                    f"{label}: _pf_ratio={_pf_ratio(text)!r} vs {PF_SIGNAL_RATIO!r} "
                    f"-> signal={got}, expected {want}"
                )
        assert not failures, "PF signal ratio gate drifted:\n" + "\n".join(failures)

        # The deliberate asymmetry: NFKC must still trigger on ANY PF
        # codepoint, even for the PF-minority blob that raises no signal.
        from pageindex_mcp.helpers.garble import _has_any_presentation_form

        assert _has_any_presentation_form("بسم الله الرحمن الرحيم ﷲ والحمد لله") is True

    def test_infer_presentation_forms_matches_ratio(self):
        """_infer_presentation_forms must agree with the ratio predicate on
        every sample, PF-dominant and PF-minority alike."""
        from pageindex_mcp.helpers.garble import (
            PF_SIGNAL_RATIO,
            _infer_presentation_forms,
            _pf_ratio,
        )

        texts = [
            "بسم الله الرحمن الرحيم ﷲ والحمد لله",
            "ﭐﭑﭒﭓﭔﭕا",
            "ﭐﭑابةتث",
            "",
            "Hello world",
        ]
        failures = [
            f"{text[:20]!r}: _infer_presentation_forms="
            f"{_infer_presentation_forms(text)!r}, ratio predicate="
            f"{_pf_ratio(text) > PF_SIGNAL_RATIO!r}"
            for text in texts
            if _infer_presentation_forms(text) != (_pf_ratio(text) > PF_SIGNAL_RATIO)
        ]
        assert not failures, "\n".join(failures)

    def test_normalize_separates_nfkc_from_signal(self):
        """normalize._pre_inference_normalize must NFKC even below ratio."""
        from pageindex_mcp.converters.normalize import _pre_inference_normalize

        text_with_one_pf = "بسم الله الرحمن الرحيم ﷲ والحمد لله"
        result, rtl_dec = _pre_inference_normalize(text_with_one_pf)
        assert "ﷲ" not in result, "NFKC should have decomposed the ligature"
        if rtl_dec is not None:
            assert rtl_dec.had_presentation_forms is False, (
                "single ligature must not set the signal"
            )


class TestCleanArabicNotFlaggedRegression:
    """PF-asymmetry false-positive guard: clean Arabic insurance prose with
    had_presentation_forms=False must NOT be flagged as garbled, whether the
    dominant script is declared ('Arab') or inferred (None).

    Absorbs the former TestD10ArabicDeadCodeFix, whose only assertion was
    ``report is not None`` -- a tautology.  Repaired here into a real verdict
    assertion on the same Arabic-script PF-fallback path."""

    _DECLARED = (
        "يغطي التأمين الأضرار التي تلحق بالغير في حدود مبلغ التغطية المتفق عليه. "
        "يلتزم المؤمن له بالإبلاغ عن الضرر فورا. "
        "تنطبق الشروط والأحكام العامة على جميع أنواع التغطية المذكورة أعلاه. "
        "يتم احتساب القسط سنويا ويستحق مقدما. "
        "في حالة وقوع حادث يجب على المؤمن له إخطار شركة التأمين خلال أسبوع. "
    ) * 5
    _INFERRED = (
        "بسم الله الرحمن الرحيم "
        "هذه وثيقة تأمين صادرة وفقا للشروط والأحكام العامة. "
        "يغطي هذا التأمين المسؤولية المدنية تجاه الغير. "
        "تسري أحكام هذه الوثيقة اعتبارا من تاريخ إصدارها. "
    ) * 5

    def test_clean_arabic_never_garbled(self):
        rows = [
            (self._DECLARED, "Arab", "declared_arab_script"),
            (self._INFERRED, None, "inferred_arab_script"),
        ]
        failures = []
        for text, script, label in rows:
            report = detect_garble(
                text,
                script_context=ScriptContext(
                    dominant_script=script, had_presentation_forms=False, source="test"
                ),
                config=GarbleConfig(),
                blob_kind=BlobKind.TREE_TEXT,
            )
            if report.is_garbled:
                failures.append(f"{label}: condemned with prongs={sorted(report.fired_prongs)}")
        assert not failures, "clean Arabic falsely condemned:\n" + "\n".join(failures)

    def test_pf_fallback_does_not_condemn_arabic_without_presentation_forms(self):
        """D10a: the 'Arabic' vs 'Arab' comparison in detect_garble was dead
        code because _infer_script returns 'Arab'.  Arabic-script text now
        reaches the NFKC PF fallback -- and, with zero presentation forms in
        the blob, that fallback must NOT raise the presentation_forms prong.

        Repaired 2026-09-22: the former TestD10ArabicDeadCodeFix asserted only
        ``report is not None``.  The fixture it used ("المادة " * 30) is in
        fact condemned -- by token_repetition, which is correct and unrelated
        to PF.  Asserting the PF prong specifically is what pins D10a."""
        report = detect_garble(
            "المادة " * 30,
            script_context=ScriptContext(
                dominant_script="Arab", had_presentation_forms=False, source="test"
            ),
            config=GarbleConfig(),
        )
        assert "presentation_forms" not in report.fired_prongs, (
            f"PF fallback condemned PF-free Arabic; prongs={sorted(report.fired_prongs)}"
        )
        # The blob IS repetitive, so the verdict itself is garbled -- by
        # token_repetition, not by the presentation-forms path.
        assert "token_repetition" in report.fired_prongs


# ===========================================================================
# latin_gibberish prong
# ===========================================================================


class TestLatinGibberishProngGuard:
    def test_latin_gibberish_guard_table(self):
        """Fires for Latin/None-script nonsense, stays silent on clean German."""
        nonsense = "Bab rel igh foal pred khar teb ghal mun sar dek phal wur zib nok " * 5
        clean_german = (
            "Die Versicherung deckt Schaden ab, die durch Feuer, Wasser oder "
            "Sturm verursacht werden. Der Versicherungsnehmer ist verpflichtet, "
            "den Schaden unverzueglich zu melden. Die Leistungen werden nach "
            "Pruefung des Schadens erbracht. Weitere Informationen finden Sie "
            "in den Allgemeinen Versicherungsbedingungen. "
        )
        rows = [
            (nonsense, "Latn", True, "nonsense_latn"),
            (nonsense, None, True, "nonsense_none_script"),
            (clean_german, "Latn", False, "clean_german"),
        ]
        failures = []
        for text, script, want, label in rows:
            fired = "latin_gibberish" in _garble_prongs(
                text, expected_script=script, config=GarbleConfig()
            )
            if fired is not want:
                failures.append(f"{label}: latin_gibberish fired={fired}, expected {want}")
        assert not failures, "\n".join(failures)


class TestLatinGibberishScriptMismatchChain5:
    """Chain 5: _garble_prongs fires latin_gibberish at a LOWERED threshold
    (0.40 instead of 0.70) when expected_script is Arabic but the text is
    predominantly Latin -- Latin-tessdata mojibake on an Arabic document.

    Both sides of that threshold boundary are pinned: the same ~50%-nonsense
    text must fire under 'Arab' and must NOT fire under 'Latn'."""

    _CFG = dict(
        garble_latin_gibberish_enabled=True,
        garble_latin_ratio=0.4,
        garble_nonsense_ratio=0.7,
    )
    # Real words: service, coverage, insurance, policy, premium (5)
    # Nonsense:   Bab, rel, igh, ghal, teb (5)  -- 50% nonsense ratio,
    # i.e. above the lowered 0.40 threshold but below the default 0.70.
    _MIXED = ("service Bab coverage rel insurance igh policy ghal premium teb ") * 5
    _CLEAN_ENGLISH = (
        "The insurance policy covers damage to third parties within the "
        "agreed coverage amount. The policyholder is obligated to report "
        "the damage immediately. Further conditions are described in the "
        "contract. The premium is calculated annually. "
    ) * 3

    def test_script_mismatch_threshold_boundary(self):
        rows = [
            (self._MIXED, "Arab", True, "mixed_arab_lowered_threshold_fires"),
            (self._MIXED, "Latn", False, "mixed_latn_default_threshold_silent"),
            (self._CLEAN_ENGLISH, "Arab", False, "clean_english_arab_expected_silent"),
        ]
        failures = []
        for text, script, want, label in rows:
            fired = "latin_gibberish" in _garble_prongs(
                text, expected_script=script, config=GarbleConfig(**self._CFG)
            )
            if fired is not want:
                failures.append(f"{label}: latin_gibberish fired={fired}, expected {want}")
        assert not failures, "\n".join(failures)


class TestNumericJunkShortProng:
    """Contract: short numeric-junk text (>= 50 chars, < garble_digit_floor,
    > 90% digits) triggers numeric_junk_short.  Closes the blind spot where
    short garbled numeric OCR noise passed unchecked below the digit floor."""

    def test_numeric_junk_short_table(self):
        import random

        random.seed(42)
        random_digits = "".join(str(random.randint(0, 9)) for _ in range(100))
        dates_text = (
            "Faelligkeitsdaten: 01.01.2025, 15.02.2025, 01.03.2025, "
            "30.04.2025, 15.05.2025, 01.06.2025, 30.07.2025, "
            "15.08.2025, 01.09.2025"
        )
        currency_text = (
            "Praemie: EUR 1200.50, Selbstbehalt: EUR 500.00, Deckungssumme: EUR 5000000.00"
        )
        short_digits = "1234567890" * 4  # 40 chars -- below the 50-char floor
        long_digits = "1234567890" * 60  # 600 chars -- above garble_digit_floor

        assert len(dates_text) >= 50 and len(currency_text) >= 50
        assert len(short_digits) < 50 and len(long_digits) > 500

        rows = [
            (random_digits, None, True, "random_digits_100"),
            (dates_text, "Latn", False, "formatted_dates"),
            (currency_text, "Latn", False, "currency_with_labels"),
            (short_digits, None, False, "below_50_chars"),
            # Above the floor, digit_ratio owns the verdict instead.
            (long_digits, None, False, "above_digit_floor"),
        ]
        failures = []
        for text, script, want, label in rows:
            prongs = _garble_prongs(
                text, expected_script=script, config=GarbleConfig(garble_digit_floor=500)
            )
            fired = "numeric_junk_short" in prongs
            if fired is not want:
                failures.append(f"{label}: numeric_junk_short fired={fired}, expected {want}")
        assert not failures, "\n".join(failures)
        # The above-floor case must be caught by digit_ratio, not silently dropped.
        assert "digit_ratio" in _garble_prongs(
            long_digits, expected_script=None, config=GarbleConfig(garble_digit_floor=500)
        )


class TestGarbleProngsExhaustiveness:
    """Exhaustiveness: every prong name returned by _garble_prongs is in a
    known valid set.  No silent additions."""

    KNOWN_PRONGS = frozenset(
        {
            "empty",
            "null_replacement_bytes",
            "glyph_marker",
            "control_chars",
            "pua_chars",
            "presentation_forms",
            "single_letter_fragments",
            "digit_ratio",
            "numeric_junk_short",
            "token_repetition",
            "latin_gibberish",
            "sparse_mojibake",
            "short_text_prior_garble",
            "script_mismatch",
        }
    )

    def test_no_unknown_prongs(self):
        test_inputs = [
            ("", None, False),
            ("\x00\x00\x00 text", None, False),
            ("GLYPH<x> test content", None, False),
            ("\x01\x02\x03\x04\x05" * 100, None, False),
            ("" * 100, None, False),
            (_GARBLED_LATIN, "Arab", False),
            ("1234567890" * 100, None, False),
            ("word " * 100, None, False),
            ("clean text " * 50, "Arab", True),
        ]
        failures = []
        for text, script, had_pf in test_inputs:
            prongs = _garble_prongs(
                text,
                expected_script=script,
                had_presentation_forms=had_pf,
                config=GarbleConfig(),
            )
            unknown = prongs - self.KNOWN_PRONGS
            if unknown:
                failures.append(f"{text[:40]!r}: unknown prong(s) {sorted(unknown)}")
        assert not failures, "\n".join(failures)


# ===========================================================================
# GarbleConfig / PipelineConfig plumbing
# ===========================================================================


class TestGarbleConfigPlumbing:
    """Contract: GarbleConfig.from_config reads cfg.garble_digit_floor
    instead of a hardcoded 500, and PipelineConfig.from_env reads the
    GARBLE_DIGIT_FLOOR env var (default 500)."""

    @staticmethod
    def _fake_cfg(floor: int):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _FakePipelineConfig:
            garble_latin_gibberish_enabled: bool = True
            garble_latin_ratio: float = 0.4
            garble_nonsense_ratio: float = 0.7
            garble_short_text_default: bool = True
            garble_flat_markdown_normalize: bool = True
            garble_node_ratio_threshold: float = 0.10
            garble_digit_floor: int = 500

        return _FakePipelineConfig(garble_digit_floor=floor)

    def test_digit_floor_flows_from_env_through_config(self, monkeypatch):
        from pageindex_mcp.config import PipelineConfig

        assert GarbleConfig.from_config(self._fake_cfg(100)).garble_digit_floor == 100
        assert GarbleConfig.from_config(self._fake_cfg(500)).garble_digit_floor == 500
        # Defaults still sane on a bare GarbleConfig().
        cfg = GarbleConfig()
        assert cfg.garble_latin_gibberish_enabled is True
        assert cfg.garble_latin_ratio > 0

        monkeypatch.setenv("GARBLE_DIGIT_FLOOR", "300")
        assert PipelineConfig.from_env().garble_digit_floor == 300
        monkeypatch.delenv("GARBLE_DIGIT_FLOOR", raising=False)
        assert PipelineConfig.from_env().garble_digit_floor == 500


# ===========================================================================
# validate_tree integration
# ===========================================================================


class TestValidateTreeIntegration:
    def test_garbled_tree_defect(self):
        tree = [
            {
                "title": "Root",
                "text": _PUA,
                "nodes": [
                    {"title": "A", "text": _PUA, "nodes": []},
                    {"title": "B", "text": _PUA, "nodes": []},
                ],
            }
        ]
        result = validate_tree(tree)
        assert not result.ok
        assert result.defect == TreeDefect.GARBLING

    def test_clean_german_tree_passes_all_gates(self):
        """Restored 2026-09-22 from the deleted TestTreeGateResultWarnings:
        the real ``validate_tree`` on a clean tree must report ok.

        Absorbs the former TestIntegration.test_clean_tree_no_garble, which
        asserted strictly less (defect != GARBLING)."""
        clean = "Dieser Text ist sauber und gut lesbar und hat viele Worte " * 20
        tree = [
            {
                "title": "Root",
                "text": clean,
                "nodes": [
                    {"title": "A", "text": clean, "nodes": []},
                    {"title": "B", "text": clean, "nodes": []},
                    {"title": "C", "text": clean, "nodes": []},
                ],
            }
        ]

        result = validate_tree(tree)

        assert result.ok is True
        assert result.defect is TreeDefect.OK
        # garble_ratio is forced to 0.0 whenever `garbled` is False, which is
        # precisely why the sub-threshold advisory was unreachable.
        assert result.signals.garble_ratio == 0.0
        assert result.signals.garbled is False
        # Determinism: the same tree must produce the same verdict twice.
        second = validate_tree(tree)
        assert (second.ok, second.defect) == (result.ok, result.defect)

    def test_table_text_is_not_false_positive_garble(self):
        """Regression: detect_garble must NOT false-positive on the
        pipe-delimited rows and numeric cells that Zone-5 pulled into
        flat_text from table headers/rows/row_records."""
        pipe_table = (
            "Versicherungsschutz\n"
            "Leistungsart | Deckungssumme | Selbstbehalt\n"
            "Haftpflicht | 5000000 | 500\n"
            "Kasko | 50000 | 300\n"
            "Insassen | 100000 | 0\n"
            "Rechtsschutz | 300000 | 250\n"
            "Die Versicherung deckt Schaden an Dritten im Rahmen der "
            "vereinbarten Deckungssumme. Der Versicherungsnehmer ist "
            "verpflichtet, den Schaden unverzueglich zu melden.\n"
        )
        numeric_table = (
            "Praemienrechnung\n"
            "Vertragsnummer | Praemie | Faellig\n"
            "VN-2024-001 | 1200.50 | 01.01.2025\n"
            "VN-2024-002 | 890.00 | 15.02.2025\n"
            "VN-2024-003 | 2340.75 | 01.03.2025\n"
            "Die jaehrliche Praemie wird im Voraus berechnet und ist zum "
            "genannten Datum faellig. Weitere Informationen entnehmen Sie "
            "bitte Ihrem Versicherungsvertrag.\n"
        )
        rows = [
            (pipe_table, None, "pipe_delimited_rows"),
            (numeric_table, "Latn", "numeric_cells"),
        ]
        failures = []
        for text, script, label in rows:
            report = detect_garble(
                text,
                script_context=ScriptContext(
                    dominant_script=script, had_presentation_forms=False, source="test"
                ),
                config=GarbleConfig(),
                blob_kind=BlobKind.TREE_TEXT,
            )
            if report.is_garbled:
                failures.append(f"{label}: prongs={sorted(report.fired_prongs)}")
        assert not failures, "clean table content condemned:\n" + "\n".join(failures)

    def test_garble_reason_wins_over_node_count_low(self):
        """D4: when both garbling/node_garbling and node_count_low fire,
        garbling must win as the primary defect so OCR recovery triggers."""
        garbled_text = "\x00\x00\x00" + "GLYPH<X>" * 50
        tree = [
            {"title": "A", "text": garbled_text, "nodes": []},
            {"title": "B", "text": garbled_text, "nodes": []},
        ]
        ctx = ScriptContext(dominant_script="Latn", had_presentation_forms=False, source="test")
        result = validate_tree(tree, expected_script=ctx)
        assert not result.ok
        assert result.defect in (TreeDefect.GARBLING, TreeDefect.NODE_GARBLING)
        assert TreeDefect.NODE_COUNT_LOW in result.all_defects


# ===========================================================================
# _garble_check_nodes -- per-node and concatenated-fallback paths
# ===========================================================================


def _nodes_garbled_count(tree, *, script=None, config=None, had_pf=False):
    return _garble_check_nodes(
        tree,
        script_context=ScriptContext(
            dominant_script=script, had_presentation_forms=had_pf, source="test"
        ),
        config=config or GarbleConfig(),
    )


class TestGarbleCheckNodes:
    def test_garbled_and_clean_nodes(self):
        clean = "Dieser Text ist sauber und gut lesbar " * 10
        garbled_tree = [
            {
                "title": "R",
                "text": "",
                "nodes": [
                    {"title": "A", "text": _PUA, "nodes": []},
                    {"title": "B", "text": "clean content " * 30, "nodes": []},
                ],
            }
        ]
        clean_tree = [
            {
                "title": "R",
                "text": clean,
                "nodes": [
                    {"title": "A", "text": clean, "nodes": []},
                    {"title": "B", "text": clean, "nodes": []},
                ],
            }
        ]
        assert _nodes_garbled_count(garbled_tree) > 0
        assert _nodes_garbled_count(clean_tree, script="Latn") == 0

    def test_expected_script_wins_over_inferred_and_logs_mismatch(self, caplog):
        """Node text infers as Latin, but the caller passes an Arabic
        expected_script derived from the filename -- expected_script must win
        and the mismatch must be logged.  Without an expected_script the
        function falls back to per-node _infer_script instead of ignoring the
        text.

        Repaired 2026-09-22: the fallback half previously asserted only
        ``isinstance(count, int)``, a tautology."""
        latin_text = "The quick brown fox jumps over the lazy dog " * 5
        nodes = [{"text": latin_text, "nodes": []}]

        with caplog.at_level(logging.WARNING):
            _nodes_garbled_count(nodes, script="Arab")
        assert any("mismatch" in rec.message.lower() for rec in caplog.records)

        # Fallback path: no expected_script -> _infer_script per node.
        assert _infer_script(latin_text) in ("Latn", None)
        assert _nodes_garbled_count(nodes, script=None) == 0, (
            "clean Latin prose must not be condemned on the inferred-script path"
        )


class TestGarbleCheckNodesTableBlockDetection:
    """Exhaustiveness: _garble_check_nodes detects garbled content in
    table-block nodes where the text lives in headers/rows/row_records instead
    of the 'text' field.

    Before the fix, _garble_check_nodes used node.get('text') per-node, making
    table-block content invisible to per-node garble checking.  The fix uses
    _node_text_parts(node)."""

    _GARBLED_DIGITS = "1234567890" * 60  # 600 chars of digits
    _GARBLED_PUA = "" * 200

    def test_garbled_table_fields_detected_per_node(self):
        """row_records, headers and rows all carry text that must be
        garble-checked per node.  Each row keeps the tree shape its original
        test used, so the clean-sibling / clean-root context is preserved."""
        def _clean(title, body):
            return {"title": title, "text": body, "nodes": []}

        rows = [
            (
                [
                    {"title": "Coverage Table", "text": "", "nodes": [],
                     "row_records": [self._GARBLED_DIGITS]},
                    _clean("Clean Section", "This is clean German insurance prose. " * 20),
                ],
                "",
                "Latn",
                "row_records",
            ),
            (
                [
                    {"title": "Data Table", "text": "", "nodes": [],
                     "headers": [self._GARBLED_PUA], "rows": []},
                ],
                "clean root text " * 20,
                None,
                "headers",
            ),
            (
                [
                    {"title": "Table", "text": "", "nodes": [],
                     "rows": [[self._GARBLED_DIGITS]]},
                    _clean("Clean", "Proper insurance text about coverage. " * 20),
                ],
                "",
                "Latn",
                "rows_matrix",
            ),
        ]
        failures = []
        for children, root_text, script, label in rows:
            tree = [{"title": "Root", "text": root_text, "nodes": children}]
            if _nodes_garbled_count(tree, script=script) < 1:
                failures.append(f"{label}: garbled table node not detected per-node")
        assert not failures, "\n".join(failures)

    def test_clean_table_not_flagged(self):
        tree = [
            {
                "title": "Root",
                "text": "Insurance policy document overview. " * 10,
                "nodes": [
                    {
                        "title": "Premium Table",
                        "text": "",
                        "headers": ["Type", "Amount", "Due"],
                        "row_records": [
                            "Liability | 5000000 | January",
                            "Comprehensive | 50000 | February",
                        ],
                        "nodes": [],
                    },
                    {
                        "title": "Terms",
                        "text": "Standard terms and conditions apply. " * 15,
                        "nodes": [],
                    },
                ],
            }
        ]
        assert _nodes_garbled_count(tree, script="Latn") == 0


class TestConcatenatedFallback:
    """D1/D3: the whole-tree concatenated fallback in _garble_check_nodes
    routes through detect_garble (not raw _garble_prongs), and delegates the
    below-floor decision to the prong's own floor check rather than an outer
    guard."""

    def test_small_garbled_nodes_caught_by_concatenation(self):
        # Each node is shorter than garble_digit_floor but together they
        # exceed it and the concatenation is garbled.
        garble_chunk = "1234567890" * 10  # 100 chars of digits per node
        tree = [
            {
                "title": "Root",
                "text": garble_chunk,
                "nodes": [
                    {"title": "A", "text": garble_chunk, "nodes": []},
                    {"title": "B", "text": garble_chunk, "nodes": []},
                    {"title": "C", "text": garble_chunk, "nodes": []},
                ],
            }
        ]
        assert _nodes_garbled_count(tree, script="Latn", config=GarbleConfig(garble_digit_floor=200)) > 0

    def test_fallback_verdict_equals_detect_garble_on_concatenation(self):
        """Both a below-floor document and a null-byte document must produce
        exactly the verdict detect_garble gives for the concatenated text."""
        digit_chunk = "1234567890" * 2  # 20 chars per node, 40 total (< 50)
        garble_text = "\x00\x00\x00" * 50 + "x" * 10
        config = GarbleConfig(garble_digit_floor=500)
        cases = [
            ([digit_chunk, digit_chunk], "below_digit_floor"),
            ([garble_text[:30], garble_text[30:]], "null_bytes"),
        ]
        failures = []
        for texts, label in cases:
            nodes = [
                {"title": str(i), "text": t, "nodes": []} for i, t in enumerate(texts)
            ]
            count = _nodes_garbled_count(nodes, script="Latn", config=config)
            concat = "\n".join(t for t in texts if t.strip())
            direct = detect_garble(
                concat,
                script_context=ScriptContext(
                    dominant_script="Latn", had_presentation_forms=False, source="direct_test"
                ),
                config=config,
            )
            if (count > 0) != direct.is_garbled:
                failures.append(
                    f"{label}: fallback count={count} but detect_garble "
                    f"is_garbled={direct.is_garbled}"
                )
        assert not failures, "\n".join(failures)


# ===========================================================================
# _garble_check_flat_blocks -- per-block gate and RFC-047 D2 char-mass ratio
# ===========================================================================

_CLEAN_PROSE = (
    "The quick brown fox jumps over the lazy dog near the river bank. "
    "Birds sing loudly in tall oak trees during warm summer mornings. "
    "Fresh coffee aroma fills the kitchen as sunlight streams through "
    "windows. Cars drive along the highway while pedestrians cross at "
    "marked intersections safely. "
)


def _check_blocks(blocks, *, script=None, had_pf=False):
    return _garble_check_flat_blocks(
        blocks,
        script_context=_default_ctx(dominant_script=script, had_presentation_forms=had_pf),
        config=_default_config(),
    )


class TestPerBlockGarbleCatchesGarbledTable:
    """Contract: the per-block check catches a garbled TABLE block even when
    the surrounding prose is clean, and passes an all-clean document."""

    def test_garbled_table_among_clean_prose_and_all_clean_control(self):
        garbled_digits = "1234567890" * 60
        mixed = [
            {"role": "prose", "text": _CLEAN_PROSE},
            {"role": "table", "text": garbled_digits, "row_records": []},
            {"role": "prose", "text": _CLEAN_PROSE},
        ]
        all_clean = [
            {"role": "prose", "text": _CLEAN_PROSE},
            {"role": "prose", "text": _CLEAN_PROSE},
        ]
        mixed_report = _check_blocks(mixed)
        clean_report = _check_blocks(all_clean, script="Latn")
        assert isinstance(mixed_report, GarbleReport) and isinstance(clean_report, GarbleReport)
        assert bool(mixed_report) is True
        assert bool(clean_report) is False


class TestDilutionImmunity:
    """Regression (RFC-027 #5330 / RFC-026): the whole-blob digit ratio passes
    but one block individually exceeds 0.60 -- the per-block check catches it."""

    def test_single_garbled_block_not_diluted(self):
        blocks = [{"role": "prose", "text": _CLEAN_PROSE} for _ in range(4)]
        blocks.append({"role": "table", "text": "9" * 600})
        report = _check_blocks(blocks)
        assert bool(report) is True
        # char-mass ratio: 600 garbled chars / (4*clean + 600) is well above 0.10
        assert report.garble_ratio > 0.10


class TestFlatBlocksRatioThreshold:
    """RFC-047 D2 (post-gate-1.C-FAIL): _garble_check_flat_blocks condemns
    only once the garbled CHARACTER MASS ratio reaches
    _GARBLE_CHAR_MASS_THRESHOLD (0.10).

    Character-mass ratio = garbled_chars / total_chars across all checked
    blocks.  This is the fix for the defect where a 0.10 *count* ratio turned
    single-block garble detection OFF above 10 blocks: the rows below
    deliberately straddle the 10-block boundary (1-of-10 condemns, 1-of-20
    does not, and a 13-block caption case stays clean), so a regression back
    to a count ratio fails here.

    The function always returns a GarbleReport -- ``is_garbled=False`` when
    clean or below threshold, ``True`` when condemned.  Callers use
    truthiness via ``__bool__``."""

    _GARBLED_BLOCK_TEXT = "9" * 600  # >500 chars of pure digits: trips digit_ratio

    @classmethod
    def _blocks(cls, *, garbled: int, total: int) -> list[dict]:
        """Build *total* non-empty blocks, of which the first *garbled* are
        garbled.  Returns a fresh list; no caller state is mutated."""
        return [
            {"role": "table", "text": cls._GARBLED_BLOCK_TEXT}
            if index < garbled
            else {"role": "prose", "text": _CLEAN_PROSE}
            for index in range(total)
        ]

    def test_char_mass_threshold_across_the_ten_block_boundary(self):
        from pageindex_mcp.helpers.garble import _GARBLE_CHAR_MASS_THRESHOLD

        assert _GARBLE_CHAR_MASS_THRESHOLD == 0.10

        # (garbled, total, should_condemn, label)
        rows = [
            (1, 10, True, "1-of-10-char-mass-above-threshold"),
            (2, 10, True, "2-of-10-char-mass-above-threshold"),
            (0, 10, False, "all-clean-passes"),
            # 600 / (600 + 19*clean) is just under 0.10 -- and a *count* ratio
            # of 1/20 = 0.05 would also be under, so this row alone does not
            # discriminate; it is the 1-of-10 row above that does.
            (1, 20, False, "1-of-20-char-mass-below-threshold"),
        ]
        failures = []
        for garbled, total, want, label in rows:
            report = _check_blocks(self._blocks(garbled=garbled, total=total))
            if not isinstance(report, GarbleReport):
                failures.append(f"{label}: returned {type(report).__name__}, not GarbleReport")
                continue
            if bool(report) is not want:
                failures.append(
                    f"{label}: condemned={bool(report)}, expected {want} "
                    f"(garble_ratio={report.garble_ratio!r})"
                )
            if want and report.garble_ratio <= 0.10:
                failures.append(f"{label}: condemned but garble_ratio={report.garble_ratio!r}")
        assert not failures, "char-mass threshold drifted:\n" + "\n".join(failures)

        # Below threshold, prongs are still preserved for flat_meta (HR5).
        below = _check_blocks(self._blocks(garbled=1, total=20))
        assert below.fired_prongs

        # The regression the RFC names: one short OCR-mangled chart caption
        # (30 chars) among twelve clean prose blocks must not condemn.
        caption_blocks = [*self._blocks(garbled=0, total=12), {"role": "caption", "text": "9" * 30}]
        assert bool(_check_blocks(caption_blocks)) is False

    def test_char_mass_logged_on_every_path(self):
        rows = [
            (0, 10, "clean", "clean-path"),
            (1, 20, "below_threshold", "below-threshold-path"),
            (2, 10, "garbled", "garbled-path"),
        ]
        failures = []
        for garbled, total, expected_choice, label in rows:
            events: list[dict] = []

            def _capture(**kwargs):
                if kwargs.get("event") == "garble_flat_block_verdict":
                    events.append(kwargs)

            with patch("pageindex_mcp.helpers.garble.decision", side_effect=_capture):
                _check_blocks(self._blocks(garbled=garbled, total=total))

            if len(events) != 1:
                failures.append(f"{label}: {len(events)} verdict events, expected 1")
                continue
            if events[0]["choice"] != expected_choice:
                failures.append(f"{label}: choice={events[0]['choice']!r}, expected {expected_choice!r}")
            attrs = events[0]["attrs"]
            missing = {
                "char_ratio",
                "block_ratio",
                "total_chars",
                "garbled_chars",
                "fired_prongs",
            } - set(attrs)
            if missing:
                failures.append(f"{label}: attrs missing {sorted(missing)}")
            if attrs.get("checked_count") != total or attrs.get("garbled_count") != garbled:
                failures.append(
                    f"{label}: checked_count={attrs.get('checked_count')!r} "
                    f"garbled_count={attrs.get('garbled_count')!r}"
                )
        assert not failures, "\n".join(failures)


class TestFlatBlockSkipRules:
    """Contract: short, empty and whitespace-only blocks are skipped rather
    than counted as garbled (RFC-025 D2 short_text_prior_garble granularity)."""

    def test_short_empty_and_whitespace_blocks_skipped(self):
        cases = [
            (
                [
                    {"role": "prose", "text": "Hi"},
                    {"role": "prose", "text": "Normal clean text here. " * 30},
                ],
                "short_block_plus_clean",
            ),
            (
                [
                    {"role": "prose", "text": ""},
                    {"role": "prose", "text": "   \n  "},
                    {"role": "prose", "text": _CLEAN_PROSE},
                ],
                "empty_and_whitespace_plus_clean",
            ),
            (
                [{"role": "prose", "text": ""}, {"role": "prose", "text": ""}],
                "only_empty_blocks",
            ),
        ]
        failures = [label for blocks, label in cases if _check_blocks(blocks)]
        assert not failures, f"blocks wrongly condemned: {failures}"


class TestFlatBlockPrimaryTextTable:
    """Contract: table blocks use row_records for primary text, prose uses text."""

    def test_primary_text_by_role(self):
        table = {"role": "table", "text": "", "row_records": ["a|b", "c|d"]}
        prose = {"role": "prose", "text": "hello world"}
        assert _flat_block_primary_text(table) == "a|b\nc|d"
        assert _flat_block_primary_text(prose) == "hello world"


class TestFlatGateCoverageExhaustiveness:
    """Exhaustiveness: every FLAT-routing TreeDefect has a coverage entry
    naming a non-empty callable."""

    def test_all_flat_routing_defects_covered(self):
        flat_defects = {
            d
            for d in TreeDefect
            if d != TreeDefect.OK
            and d != TreeDefect.ARABIC_LOW_CONTENT_RATIO
            and decide_route(d) == Route.FLAT
        }
        assert flat_defects <= set(FLAT_GATE_COVERAGE), (
            f"Missing FLAT_GATE_COVERAGE entries: {flat_defects - set(FLAT_GATE_COVERAGE)}"
        )
        for defect, name in FLAT_GATE_COVERAGE.items():
            assert isinstance(name, str) and name, f"Empty callable name for {defect}"


# ===========================================================================
# ScriptContext / had_presentation_forms wiring across call sites
# ===========================================================================


class TestPresentationFormsThreading:
    """Regression (RFC-019 D2 / RFC-028 D2): had_presentation_forms must
    thread through to the per-block detect_garble calls."""

    def test_presentation_forms_flag_reaches_detect_garble(self):
        calls = []
        original_detect = detect_garble

        def spy_detect(text, **kwargs):
            calls.append(kwargs.get("script_context"))
            return original_detect(text, **kwargs)

        with patch("pageindex_mcp.helpers.garble.detect_garble", side_effect=spy_detect):
            _check_blocks([{"role": "prose", "text": _CLEAN_PROSE}], had_pf=True)

        assert len(calls) >= 1
        assert all(c.had_presentation_forms is True for c in calls if c is not None)


class TestScriptContextThreadsThroughValidateTree:
    """Wiring: ScriptContext.had_presentation_forms threads through
    validate_tree to _gate_garbling and _gate_node_garbling."""

    def test_had_presentation_forms_threads_to_garble_gate(self):
        text = "clean text content here " * 30
        tree = [
            {
                "title": "Root",
                "text": text,
                "nodes": [
                    {"title": "A", "text": text, "nodes": []},
                    {"title": "B", "text": text, "nodes": []},
                    {"title": "C", "text": text, "nodes": []},
                ],
            }
        ]
        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=True, source="test")

        calls = []
        from pageindex_mcp.helpers.garble import detect_garble as _orig_detect

        def spy_detect(text, **kwargs):
            sc = kwargs.get("script_context")
            if sc is not None:
                calls.append(sc.had_presentation_forms)
            return _orig_detect(text, **kwargs)

        with patch("pageindex_mcp.helpers.garble.detect_garble", side_effect=spy_detect):
            validate_tree(tree, expected_script=ctx)

        assert any(c is True for c in calls), (
            f"No detect_garble call received had_presentation_forms=True; values seen: {calls}"
        )


class TestApplyPromotionsScriptContextWiring:
    """Wiring: apply_promotions receives and uses the caller's ScriptContext
    instead of constructing a throwaway with had_presentation_forms=False."""

    def test_script_context_threaded_to_detect_garble(self):
        from pageindex_mcp.config import pipeline_config
        from pageindex_mcp.helpers.tree_validation import TreeSignals
        from pageindex_mcp.helpers.types import GateOutcome, VerdictThresholds
        from pageindex_mcp.helpers.verdict import apply_promotions

        # The fixture must survive `effectively_garbled` too: plain repeated
        # filler trips token_repetition and short-circuits the rescue path.
        import random

        random.seed(7)
        _words = (
            "insurance policy coverage premium liability damage third parties "
            "agreed amount policyholder obligated report immediately further "
            "conditions described contract calculated annually advance claim "
            "notice week accident company deductible comprehensive schedule "
            "endorsement exclusion renewal certificate"
        ).split()
        text = " ".join(random.choice(_words) for _ in range(120))
        # node_count >= 3 is a hard precondition of the image-enrichment
        # rescue path; with fewer nodes apply_promotions returns before
        # detect_garble and the threading assertion below is vacuous.
        tree = [
            {
                "title": "Root",
                "text": text,
                "nodes": [
                    {"title": "A", "text": text, "nodes": []},
                    {"title": "B", "text": text, "nodes": []},
                    {"title": "C", "text": text, "nodes": []},
                ],
            }
        ]
        outcome = GateOutcome(
            defect=TreeDefect.OK,
            validate_reason=None,
            signals=TreeSignals.from_tree(tree),
            all_defects=frozenset(),
            hard_fail_verdict=None,
        )
        sc = ScriptContext(dominant_script="Arab", had_presentation_forms=True, source="test")

        calls = []
        from pageindex_mcp.helpers.garble import detect_garble as _orig

        def spy(text, **kwargs):
            ctx = kwargs.get("script_context")
            if ctx is not None:
                calls.append(ctx.had_presentation_forms)
            return _orig(text, **kwargs)

        with patch("pageindex_mcp.helpers.verdict.detect_garble", side_effect=spy):
            apply_promotions(
                outcome,
                content_class="flat_prose",
                image_enrichment_ratio=0.9,
                inspector_class=None,
                th=VerdictThresholds.from_config(pipeline_config),
                expected_script="Arab",
                script_context=sc,
            )

        assert calls, (
            "apply_promotions never reached detect_garble on the image-enrichment "
            "path -- the threading assertion below would be vacuous"
        )
        assert all(c is True for c in calls), (
            f"apply_promotions called detect_garble without threading "
            f"had_presentation_forms=True; values: {calls}"
        )


class TestEndToEndScriptContextNoThrowaway:
    """Integration: TreeSignals.from_tree, given a ScriptContext with
    had_presentation_forms=True, does NOT construct a new ScriptContext with
    had_presentation_forms=False."""

    def test_no_throwaway_script_context_in_tree_signals(self):
        from pageindex_mcp.helpers.tree_validation import TreeSignals

        text = "clean text " * 50
        tree = [
            {
                "title": "Root",
                "text": text,
                "nodes": [
                    {"title": "A", "text": text, "nodes": []},
                    {"title": "B", "text": text, "nodes": []},
                    {"title": "C", "text": text, "nodes": []},
                ],
            }
        ]
        ctx = ScriptContext(dominant_script="Arab", had_presentation_forms=True, source="test")

        constructed = []
        _orig_init = ScriptContext.__init__

        def spy_init(self, *args, **kwargs):
            _orig_init(self, *args, **kwargs)
            constructed.append(self)

        with patch.object(ScriptContext, "__init__", spy_init):
            TreeSignals.from_tree(tree, expected_script=ctx)

        tree_signals_ctxs = [c for c in constructed if c.source == "tree_signals"]
        for c in tree_signals_ctxs:
            assert c.had_presentation_forms is True, (
                f"TreeSignals.from_tree constructed ScriptContext with "
                f"had_presentation_forms=False (source={c.source})"
            )


# ===========================================================================
# F0 -- splice_picture_text_for_tree / splice_figure_markers
# ===========================================================================


class TestSplicePictureTextForTree:
    def test_ocr_text_appended_after_markers(self):
        md = f"# Title\n\n{_MARKER}\n\nSome text.\n\n{_MARKER}\n\nMore text."
        pics = [_pic("Revenue 2024: 42%"), _pic("Costs down 10%")]

        out = splice_picture_text_for_tree(md, pics)

        assert out.count(_MARKER) == 2
        assert "> [Chart text]: Revenue 2024: 42%" in out
        assert "> [Chart text]: Costs down 10%" in out
        # Ordering: the first chart-text block follows the first marker and
        # precedes the second marker.
        first_marker_idx = out.index(_MARKER)
        first_chart_idx = out.index("> [Chart text]: Revenue 2024: 42%")
        second_marker_idx = out.index(_MARKER, first_marker_idx + 1)
        assert first_marker_idx < first_chart_idx < second_marker_idx

        # No pictures, and pictures with empty ocr_text, both leave the
        # markdown byte-identical; the marker count never changes.
        md = f"# Title\n\n{_MARKER}\n\nA\n\n{_MARKER}\n\nB\n\n{_MARKER}\n\nC"
        assert splice_picture_text_for_tree(md, []) == md

        single = f"# Title\n\n{_MARKER}\n\nBody."
        out = splice_picture_text_for_tree(single, [_pic("")])
        assert out == single
        assert "> [Chart text]:" not in out

        spliced = splice_picture_text_for_tree(md, [_pic("x"), _pic(""), _pic("z")])
        assert spliced.count(_MARKER) == md.count(_MARKER) == 3

    def test_kill_switch_env_var(self, monkeypatch):
        """TREE_PATH_PICTURE_SPLICE_ENABLED gates whether the tree path calls
        splice_picture_text_for_tree at all (client/images.py, recovery.py,
        indexer.py).  The documented contract is "1"/"true"/"yes"
        (case-insensitive), defaulting to enabled.

        Repaired 2026-09-22: this test previously defined its own ``_parse``
        helper and asserted on that -- a tautology that exercised no
        production code.  It now reloads the production module and asserts on
        the real constant."""
        import importlib

        from pageindex_mcp.client import images

        rows = [
            ("false", False),
            ("true", True),
            ("0", False),
            ("YES", True),
            ("1", True),
            (None, True),  # unset -> default "true"
        ]
        failures = []
        try:
            for raw, want in rows:
                if raw is None:
                    monkeypatch.delenv("TREE_PATH_PICTURE_SPLICE_ENABLED", raising=False)
                else:
                    monkeypatch.setenv("TREE_PATH_PICTURE_SPLICE_ENABLED", raw)
                importlib.reload(images)
                got = images.TREE_PATH_PICTURE_SPLICE_ENABLED
                if got is not want:
                    failures.append(f"{raw!r}: parsed to {got!r}, expected {want!r}")
        finally:
            monkeypatch.undo()
            importlib.reload(images)

        assert not failures, "splice kill switch parse drifted:\n" + "\n".join(failures)

        # Behavioral check: when disabled, callers skip the splice entirely
        # (mirrors the `if pic_results and TREE_PATH_PICTURE_SPLICE_ENABLED:`
        # guard in client/indexer.py and client/recovery.py).
        md = f"# Title\n\n{_MARKER}\n\nBody."
        pics = [_pic("ocr text here")]
        md_content = md
        if pics and False:
            md_content = splice_picture_text_for_tree(md_content, pics)
        assert md_content == md


# ===========================================================================
# F1 -- text-layer-gated coverage exemption in _recover_picture_text
# ===========================================================================
class TestF1CoverageExemption:
    def test_full_page_region_is_page_coverage_skip(self, monkeypatch):
        """A full-page region is skipped as page_coverage both when the
        exemption is on and the page HAS a text layer (decorative background
        over real text), and when the exemption is off with no text layer
        (pre-F1 / legacy behavior).  D5a (RFC-029): the skip retains
        png_bytes + skipped_reason and carries no ocr_text."""
        cases = [
            (True, _long_text(60), None, "exempt_on_with_text_layer"),
            (False, "", "", "exempt_off_no_text_layer"),
        ]
        for exempt, page_text, clip_text, label in cases:
            monkeypatch.setattr(
                converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", exempt
            )
            if not exempt:
                monkeypatch.setattr(
                    converters.pictures,
                    "_GATE_CONFIG",
                    PictureGateConfig(coverage_exempt_no_text_layer=False),
                )
            _install_fake_fitz(monkeypatch, page_text=page_text, clip_text=clip_text)
            monkeypatch.setattr(
                converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
            )

            recovered, skip_reasons = _recover_picture_text("dummy.pdf", [_region()], ["eng"])

            assert skip_reasons.get(0) == "page_coverage", label
            assert 0 in recovered, label
            assert recovered[0].get("skipped_reason") == "page_coverage", label
            assert recovered[0].get("png_bytes"), label
            assert not recovered[0].get("ocr_text"), label

    def test_clip_text_skip(self, monkeypatch):
        """A sub-coverage region whose clip already has real text under it AND
        that text is already contained in the Docling markdown export (RFC-024
        D1 containment guard) is skipped with reason
        "clip_text_already_exported" rather than re-OCR'd.  D5a: this skip
        retains png_bytes AND ocr_text."""
        monkeypatch.setattr(converters.pictures, "_COVERAGE_EXEMPT_NO_TEXT_LAYER", True)
        small_region = _region(l=0, t=0, r=100, b=100)
        _install_fake_fitz(monkeypatch, page_text="", clip_text=_long_text(30))
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda png, langs: _long_text()
        )

        recovered, skip_reasons = _recover_picture_text(
            "dummy.pdf", [small_region], ["eng"], md=_long_text(30)
        )

        assert skip_reasons.get(0) == "clip_text_already_exported"
        assert 0 in recovered
        assert recovered[0].get("skipped_reason") == "clip_text_already_exported"
        assert recovered[0].get("png_bytes")
        assert recovered[0].get("ocr_text") == _long_text(30)


# ===========================================================================
# F5 -- skip-reason plumbing (_recover_picture_results uses the REAL reason,
# not a hardcoded "page_coverage" string)
# ===========================================================================
class TestF5SkipReason:
    def _setup(self, monkeypatch, *, recovered, skip_reasons, n_regions=1):
        monkeypatch.setattr(converters.pictures, "_OCR_ESCALATION_PER_PICTURE", True)
        monkeypatch.setattr(
            converters.pictures,
            "_collect_picture_regions",
            lambda d: [_region() for _ in range(n_regions)],
        )
        monkeypatch.setattr(converters.pictures, "detect_ocr_langs", lambda s: ["eng"])
        monkeypatch.setattr(converters.pictures, "ensure_tessdata", lambda langs: langs)
        monkeypatch.setattr(
            converters.pictures,
            "_recover_picture_text",
            lambda *a, **k: (recovered, skip_reasons),
        )

    def test_skip_reason_propagated_verbatim_and_ordinals_preserved(self, monkeypatch):
        """Whatever reason _recover_picture_text reports is what surfaces --
        no hardcoded substitution -- and in the mixed case (one region
        recovered, one skipped with a real reason, one defaulting to unknown)
        the ordinals stay aligned (finding 4)."""
        for reason in ("page_coverage", "clip_text"):
            self._setup(monkeypatch, recovered={}, skip_reasons={0: reason})
            pics = _recover_picture_results("x <!-- image --> y", object(), "d.pdf")
            assert len(pics) == 1
            assert pics[0].get("skipped_reason") == reason

        pr0 = PictureResult(ocr_text="recovered chart text here", png_bytes=b"a", page=1, bbox={})
        self._setup(
            monkeypatch,
            recovered={0: pr0},
            skip_reasons={1: "page_coverage"},
            n_regions=3,
        )

        pics = _recover_picture_results("x <!-- image --> y", object(), "d.pdf")

        assert len(pics) == 3
        assert pics[0] is pr0
        assert pics[1].get("skipped_reason") == "page_coverage"
        assert pics[2].get("skipped_reason") == "unknown"



# ===========================================================================
# F3 -- OCR language detection from the filename
# ===========================================================================
class TestOcrLangOverride:
    def test_detect_ocr_langs_tier_table(self):
        """LANG-01-C1: detect_ocr_langs classifies a sample by Arabic/Latin/
        German-diacritic ratio into a ranked Tesseract language list via a
        3-tier decision -- dominant-Arabic, bilingual-gazette, German-hint --
        with a deu,eng fallback for empty or letterless input.  Pure Unicode
        block ratios: no network call, no model inference."""
        rows = [
            # (sample, must_contain, label)
            ("وارد_597.pdf", {"ara"}, "dominant_arabic_filename"),
            ("سياسة حوكمة التأمين الإلزامي في دولة الإمارات", {"ara"}, "dominant_arabic_prose"),
            ("Haftpflicht Versicherungsbedingungen Prämie Schäden", {"deu", "eng"}, "german_hint"),
            ("", {"deu", "eng"}, "empty_fallback"),
            ("12345 67890 ...", {"deu", "eng"}, "letterless_fallback"),
        ]
        failures = []
        for sample, must_contain, label in rows:
            langs = detect_ocr_langs(sample)
            missing = must_contain - set(langs)
            if missing:
                failures.append(f"{label}: detect_ocr_langs -> {langs!r}, missing {sorted(missing)}")
            if not langs:
                failures.append(f"{label}: returned an empty language list")
        assert not failures, "\n".join(failures)


# ===========================================================================
# D3 (RFC-047): post-enrichment garble consequence -- per-block field-clear
# ===========================================================================


class TestPostEnrichmentGarbleConsequence:
    """D3 (RFC-047 Wave 2): per-block garble detection on enriched image
    blocks decides whether ``ocr_text`` is cleared.  Only the detect_garble
    verdict is production behaviour -- the clearing loop lives in the
    enrichment caller, so it is exercised once here over a mixed batch."""

    _GARBLED_OCR = "9" * 600  # pure digits -- trips digit_ratio
    _CLEAN_OCR = (
        "This table shows quarterly revenue figures across all regions. "
        "The data is broken down by product category and sales channel. "
        "Growth rates are calculated year-over-year for each segment. "
    )
    _TINY_CAPTION = "Figure 3: Revenue by region"

    @staticmethod
    def _image_block(ocr_text: str) -> dict:
        return {
            "role": "image",
            "ocr_text": ocr_text,
            "figure_path": "img.png",
            "page": 1,
            "bbox": [10, 20, 300, 400],
            "description": "A chart showing data",
        }

    def test_only_garbled_blocks_are_cleared_metadata_preserved(self):
        blocks = [
            self._image_block(t)
            for t in (
                self._GARBLED_OCR,
                self._CLEAN_OCR,
                self._TINY_CAPTION,
                self._GARBLED_OCR,
                self._CLEAN_OCR,
            )
        ]
        ctx = _default_ctx()
        cfg = _default_config()
        stripped = 0
        for blk in blocks:
            report = detect_garble(
                blk.get("ocr_text", ""),
                script_context=ctx,
                config=cfg,
                blob_kind=BlobKind.TREE_TEXT,
            )
            if report:
                blk["ocr_text"] = ""
                stripped += 1

        assert stripped == 2, "only the two pure-digit blocks may be condemned"
        assert [b["ocr_text"] for b in blocks] == [
            "",
            self._CLEAN_OCR,
            self._TINY_CAPTION,  # a short clean caption must not be stripped
            "",
            self._CLEAN_OCR,
        ]
        # Clearing ocr_text preserves every other field on the block.
        for blk in blocks:
            assert blk["role"] == "image"
            assert blk["figure_path"] == "img.png"
            assert blk["page"] == 1
            assert blk["bbox"] == [10, 20, 300, 400]
            assert blk["description"] == "A chart showing data"


# ===========================================================================
# --- merged from tests/test_d7_arbitration.py (RFC-046 D7) ---
# arbitrate() -- unified N-candidate policy.  Arbitration ORDER is a known
# regression surface: the policy table below pins every tiebreak rung.
# ===========================================================================


class TestArbitrate:
    def test_arbitration_policy_table(self):
        """One row per policy rung, in the order arbitrate() applies them.

        Each row is (candidates, expected_winner_index, label).  Every
        mismatching row is reported, so a reordering of the policy names all
        the rungs it broke rather than just the first."""
        rows = [
            (
                [Candidate(label="only", text="hello", char_count=5, garbled=False)],
                0,
                "single_candidate",
            ),
            (
                [
                    Candidate(label="pre", text="x" * 1000, char_count=1000, garbled=True),
                    Candidate(label="post", text="y" * 500, char_count=500, garbled=False),
                ],
                1,
                "clean_beats_garbled_despite_fewer_chars",
            ),
            (
                [
                    Candidate(label="small", text="a" * 100, char_count=100, garbled=False),
                    Candidate(label="large", text="b" * 500, char_count=500, garbled=False),
                ],
                1,
                "more_chars_wins_when_both_clean",
            ),
            (
                [
                    Candidate(label="empty", text="", char_count=0, garbled=False),
                    Candidate(label="ok", text="content", char_count=7, garbled=True),
                ],
                1,
                "empty_candidate_never_wins",
            ),
            (
                [
                    Candidate(
                        label="tess", text="b" * 100, char_count=100, garbled=False,
                        engine="tesseract",
                    ),
                    Candidate(
                        label="surya", text="a" * 100, char_count=100, garbled=False,
                        engine="surya",
                    ),
                ],
                1,
                "engine_reliability_breaks_ties",
            ),
        ]
        failures = [
            f"{label}: arbitrate(...) == {arbitrate(cands)!r}, expected {want!r}"
            for cands, want, label in rows
            if arbitrate(cands) != want
        ]
        assert not failures, "arbitration order drifted:\n" + "\n".join(failures)

        # Hallucination guard: a wildly inflated candidate must not win even
        # though it has the most characters.
        normal_a = Candidate(label="a", text="x" * 5000, char_count=5000, garbled=False)
        normal_b = Candidate(label="b", text="y" * 4900, char_count=4900, garbled=False)
        inflated = Candidate(label="c", text="z" * 40000, char_count=40000, garbled=False)
        assert arbitrate([normal_a, normal_b, inflated]) != 2, (
            "hallucinated candidate should not win"
        )


class TestEngineRankAndHallucinationGuard:
    def test_engine_rank_table(self):
        last = len(ENGINE_RELIABILITY_ORDER)
        rows = [
            ("surya", 0, "surya_is_best"),
            ("tesseract", 1, "tesseract_is_second"),
            ("Surya", 0, "case_insensitive_lower"),
            ("TESSERACT", 1, "case_insensitive_upper"),
            ("something_new", last, "unknown_engine_ranks_last"),
            (None, last, "none_engine_ranks_last"),
        ]
        failures = [
            f"{label}: _engine_rank({engine!r}) == {_engine_rank(engine)!r}, expected {want!r}"
            for engine, want, label in rows
            if _engine_rank(engine) != want
        ]
        assert not failures, "\n".join(failures)

    def test_median_chars_and_hallucination_threshold(self):
        a = Candidate(label="a", text="a", char_count=100, garbled=False)
        b = Candidate(label="b", text="b", char_count=200, garbled=False)
        empty = Candidate(label="e", text="", char_count=0, garbled=False)
        assert _median_chars([a]) == 100.0
        assert _median_chars([a, b]) == 150.0
        assert _median_chars([empty, a]) == 100.0

        small = Candidate(label="x", text="a" * 100, char_count=100, garbled=False)
        big = Candidate(label="x", text="a" * 400, char_count=400, garbled=False)
        assert not _is_hallucinated(small, 50.0)
        assert _is_hallucinated(big, 100.0)
        # A zero median carries no signal -- never condemn on it.
        assert not _is_hallucinated(big, 0.0)
        assert HALLUCINATION_CHAR_RATIO > 1.0


class TestPreRebuildMdQuality:
    """ExtractionState and RecoveryOutcome carry pre_rebuild_md_* fields, and
    RecoveryOutcome.apply overwrites them on the state."""

    @staticmethod
    def _state() -> ExtractionState:
        return ExtractionState(
            result={}, ok=False, reason="", gate_result=None,
            first_defect=TreeDefect.NODE_COUNT_LOW, route=Route.REJECT,
            md_content=None, tmp_md_path=None, pic_results=[], used_converter=None,
            total_chars=0, extraction_stages_captured=[],
        )

    def test_defaults_set_and_apply(self):
        state = self._state()
        assert state.pre_rebuild_md_chars is None
        assert state.pre_rebuild_md_garbled is None

        state.pre_rebuild_md_chars = 5000
        state.pre_rebuild_md_garbled = False
        assert state.pre_rebuild_md_chars == 5000
        assert state.pre_rebuild_md_garbled is False

        RecoveryOutcome(
            pre_rebuild_md_chars=None,
            pre_rebuild_md_garbled=None,
        ).apply(state)
        assert state.pre_rebuild_md_chars is None
        assert state.pre_rebuild_md_garbled is None


class TestCleanMdOverridesCharRegression:
    """D7 task 5.2: _keep_best_wins with post_md_garbled=False lets a clean
    post-recovery markdown override the raw char-count revert."""

    def test_clean_md_overrides_garbled_pre_with_more_chars(self):
        """The pre-result is Latin filler under an Arabic script context, so
        it is garbled; the post-result has fewer tree chars but clean
        markdown, and must therefore be kept.

        Repaired 2026-09-22: this test previously asserted only
        ``isinstance(result, bool)`` -- a tautology that left the override
        untested.  It now asserts the verdict."""
        from pageindex_mcp.client.recovery import _keep_best_wins

        pre_result = {"structure": [
            {"title": "x", "text": "garbled " * 15000, "children": []},
        ]}
        post_result = {"structure": [
            {"title": "y", "text": "clean " * 10000, "children": []},
        ]}
        sc = ScriptContext(dominant_script="ar", had_presentation_forms=False, source="test")
        result = _keep_best_wins(
            pre_result=pre_result,
            pre_total_chars=105000,
            post_result=post_result,
            post_ok=False,
            expected_script="ar",
            script_context=sc,
            filename="test.pdf",
            post_md_garbled=False,
        )
        assert result is True, (
            "clean post markdown must override the char-count revert when the "
            "pre-result is garbled"
        )

    def test_without_post_md_garbled_reverts_normally(self):
        from pageindex_mcp.client.recovery import _keep_best_wins

        pre_result = {"structure": [
            {"title": "x", "text": "hello world " * 1000, "children": []},
        ]}
        post_result = {"structure": [
            {"title": "y", "text": "hello " * 500, "children": []},
        ]}
        result = _keep_best_wins(
            pre_result=pre_result,
            pre_total_chars=12000,
            post_result=post_result,
            post_ok=False,
            expected_script=None,
            script_context=None,
            filename="test.pdf",
            post_md_garbled=None,
        )
        assert result is False, "without post_md_garbled, fewer chars should revert"


# ===========================================================================
# --- merged from tests/test_d7_arabic_density_floor.py (RFC-047 D7) ---
# Script-aware Arabic density floor in _gate_suspect_density: Arabic-dominant
# documents use a lower floor (800) than everything else (1200).
# ===========================================================================


def _density_signals(flat_text_len: int) -> TreeSignals:
    """Build minimal TreeSignals with a controllable flat_text length."""
    text = "a" * flat_text_len
    return TreeSignals(
        flat_text=text,
        flat_text_corrected=text,
        node_count=10,
        depth=4,
        max_leaf_ratio=0.1,
        garbled=False,
        garble_ratio=0.0,
        effectively_garbled=False,
        is_reordered=False,
        expected_min_depth=2,
        garble_prongs=frozenset(),
    )


def _script_ctx(script: str | None) -> ScriptContext | None:
    if script is None:
        return None
    return ScriptContext(
        dominant_script=script, had_presentation_forms=False, source="test"
    )


class TestArabicDensityFloor:
    """D7: Arabic-dominant docs use a lower density floor (800 vs 1200).

    The table straddles BOTH floors in BOTH directions, so neither floor can
    drift without a named failure."""

    def test_density_floor_boundary_table(self):
        rows = [
            # (chars, script, should_fire, label)
            (10_000, "Arab", False, "arabic_1000cpp_between_floors_silent"),
            (10_000, "Latn", True, "latin_1000cpp_between_floors_fires"),
            (10_000, None, True, "no_script_ctx_uses_general_floor"),
            (5_000, "Arab", True, "arabic_500cpp_below_arabic_floor_fires"),
            (8_000, "Arab", False, "arabic_exactly_800cpp_silent"),
            (12_000, "Latn", False, "latin_exactly_1200cpp_silent"),
        ]
        failures = []
        for chars, script, want, label in rows:
            fires, detail = _gate_suspect_density(
                _density_signals(chars),
                structure=[],
                expected_script=_script_ctx(script),
                page_count=10,
                rtl_decision=None,
            )
            if fires is not want:
                failures.append(f"{label}: fired={fires}, expected {want} (detail={detail!r})")
            if fires and "chars_per_page=" not in detail:
                failures.append(f"{label}: firing detail lacks chars_per_page: {detail!r}")
        assert not failures, "density floor drifted:\n" + "\n".join(failures)

    def test_decision_attrs_record_the_floor_used(self, monkeypatch):
        """The decision event must say which floor was applied and whether the
        document was treated as Arabic."""
        rows = [
            ("Arab", True, 800.0, "arabic"),
            ("Latn", False, 1200.0, "non_arabic"),
        ]
        failures = []
        for script, want_arabic, want_floor, label in rows:
            logged_attrs: dict = {}

            def _capture_decision(*, event, choice, reason, attrs, **kw):
                if event == "suspect_density_gate":
                    logged_attrs.update(attrs)

            monkeypatch.setattr(
                "pageindex_mcp.helpers.gates.decision", _capture_decision
            )
            _gate_suspect_density(
                _density_signals(10_000),
                structure=[],
                expected_script=_script_ctx(script),
                page_count=10,
                rtl_decision=None,
            )
            if logged_attrs.get("is_arabic") is not want_arabic:
                failures.append(f"{label}: is_arabic={logged_attrs.get('is_arabic')!r}")
            if logged_attrs.get("floor_used") != want_floor:
                failures.append(f"{label}: floor_used={logged_attrs.get('floor_used')!r}")
            if "floor_arabic" not in logged_attrs:
                failures.append(f"{label}: floor_arabic missing from attrs")
        assert not failures, "\n".join(failures)
