# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Content recovery and RFC recovery tests."""

from __future__ import annotations

import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pageindex_mcp.converters as converters
from pageindex_mcp.converters import (
    _inject_arabic_structural_headings,
    chunked_docling_timeout_s,
    detect_ocr_langs,
    probe_conversion_route,
)
from pageindex_mcp.helpers import (
    _OVERSIZED_ORDINAL_RE,
    _ReasonPolicy,
    _Unset,
    _flatten_tree_text,
    _ordinal_value,
    _word_has_reversed_morphology,
    BULK_PROFILE,
    ExtractionState,
    GATES,
    GateSpec,
    RecoveryOutcome,
    Route,
    split_oversized_leaf_nodes,
    TreeDefect,
    TreeGateResult,
    validate_tree,
)
from pageindex_mcp.worker import (
    CHILD_TIMEOUT,
    _run_converter_subprocess,
)
from tests._garble_compat import check_garble


# --- from test_recovery.py ---

_RETRY_POLICIES = frozenset({_ReasonPolicy.RETRY_OCR, _ReasonPolicy.RETRY_RTL})
_GATES_BY_DEFECT: dict[TreeDefect, GateSpec] = {g.defect: g for g in GATES}
_RECOVERY_GATES = [g for g in GATES if g.recovery_fns]


def _make_state(
    ok: bool = False,
    route: Route = Route.REJECT,
    first_defect: TreeDefect = TreeDefect.NODE_COUNT_LOW,
    gate_result: TreeGateResult | None = None,
    reason: str = "",
    bidi_renorm_applied: bool = False,
    tmp_md_path: str | None = None,
) -> ExtractionState:
    return ExtractionState(
        result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 200, "nodes": []}]},
        ok=ok,
        reason=reason or first_defect.value,
        gate_result=gate_result,
        first_defect=first_defect,
        route=route,
        md_content="# test content",
        tmp_md_path=tmp_md_path,
        pic_results=[],
        used_converter="pymupdf4llm",
        total_chars=200,
        extraction_stages_captured=[],
        bidi_renorm_applied=bidi_renorm_applied,
    )


def _make_eligibility_state(defect: TreeDefect, ok: bool = False) -> ExtractionState:
    return ExtractionState(
        result={},
        ok=ok,
        reason=defect.value,
        gate_result=None,
        first_defect=defect,
        route=MagicMock(),
        md_content=None,
        tmp_md_path=None,
        pic_results=[],
        used_converter=None,
        total_chars=0,
        extraction_stages_captured=[],
    )


# ===========================================================================
# GateSpec recovery wiring
# ===========================================================================


class TestGateSpecRecoveryWiring:
    def test_retry_gates_have_recovery_wiring(self):
        for g in GATES:
            if g.policy in _RETRY_POLICIES:
                assert g.recovery_fns
                assert g.recovery_eligible is not None

    def test_reverse_recovery_fns_implies_eligible(self):
        for g in GATES:
            if g.recovery_fns:
                assert g.recovery_eligible is not None


# ===========================================================================
# Eligibility predicates
# ===========================================================================


class TestEligibility:
    def test_garble_gate_accepts_garbling(self):
        gate = _GATES_BY_DEFECT[TreeDefect.GARBLING]
        state = _make_eligibility_state(TreeDefect.GARBLING, ok=False)
        assert gate.recovery_eligible(state)

    def test_garble_gate_rejects_unrelated(self):
        gate = _GATES_BY_DEFECT[TreeDefect.GARBLING]
        state = _make_eligibility_state(TreeDefect.RTL_REVERSAL, ok=False)
        assert not gate.recovery_eligible(state)


# ===========================================================================
# Regression guards
# ===========================================================================


class TestRegressionGuards:
    def test_persist_fail_no_recovery(self):
        pf = [g for g in GATES if g.policy == _ReasonPolicy.PERSIST_FAIL]
        assert len(pf) >= 3
        for g in pf:
            assert not g.recovery_fns

    def test_rtl_reversal_fires_rtl_recovery(self):
        rtl = _GATES_BY_DEFECT[TreeDefect.RTL_REVERSAL]
        assert rtl.policy == _ReasonPolicy.RETRY_RTL
        assert "_recover_rtl_repair" in rtl.recovery_fns


# ===========================================================================
# Recovery severity ordering
# ===========================================================================


class TestSeverityOrdering:
    def test_gates_sorted_by_severity(self):
        active = [g for g in GATES if g.gate_fn is not None]
        severities = [g.severity for g in active]
        assert severities == sorted(severities)


# ===========================================================================
# RecoveryOutcome
# ===========================================================================


class TestRecoveryOutcome:
    def test_frozen(self):
        ro = RecoveryOutcome(ok=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ro.ok = False

    def test_defaults_to_unset(self):
        ro = RecoveryOutcome()
        for f in dataclasses.fields(ro):
            assert isinstance(getattr(ro, f.name), _Unset)

    def test_apply_single_field(self):
        state = _make_state(ok=False, route=Route.REJECT)
        RecoveryOutcome(ok=True).apply(state)
        assert state.ok is True
        assert state.route == Route.REJECT

    def test_explicit_none_distinct_from_unset(self):
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        state = _make_state(gate_result=gate)
        RecoveryOutcome().apply(state)
        assert state.gate_result is gate
        RecoveryOutcome(gate_result=None).apply(state)
        assert state.gate_result is None

    def test_full_snapshot_revert(self):
        from pageindex_mcp.script import RtlDecision

        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        pre_retry = RecoveryOutcome(
            result={"structure": [{"node_id": "1", "title": "Pre", "text": "aaa", "nodes": []}]},
            ok=True,
            reason="ok",
            gate_result=gate,
            total_chars=48000,
            md_content="# pre",
            pic_results=[{"page": 1}],
            used_converter="docling",
            route=Route.TREE,
            rtl_decision=RtlDecision(
                reversed=False, repair_effective=True, sampled=5, method="nfkc"
            ),
            tmp_md_path="/tmp/pre.md",
            bidi_renorm_applied=True,
        )
        state = _make_state(ok=False, route=Route.REJECT, tmp_md_path="/tmp/post.md")
        pre_retry.apply(state)
        assert state.ok is True
        assert state.route == Route.TREE
        assert state.total_chars == 48000


# ===========================================================================
# ExtractionState field contract
# ===========================================================================


class TestExtractionState:
    def test_gate_result_retained(self):
        fields = {f.name for f in dataclasses.fields(ExtractionState)}
        assert "gate_result" in fields

    def test_bidi_renorm_applied_defaults_false(self):
        assert _make_state().bidi_renorm_applied is False


# ===========================================================================
# Dead-gate regression
# ===========================================================================


class TestDeadGateRegression:
    def test_validate_tree_never_returns_arabic_low_content(self):
        tree = [
            {
                "title": "Root",
                "body": "",
                "nodes": [
                    {"title": "A", "body": "hello " * 50, "nodes": []},
                    {"title": "B", "body": "world " * 50, "nodes": []},
                    {"title": "C", "body": "test " * 50, "nodes": []},
                ],
            }
        ]
        assert validate_tree(tree).defect != TreeDefect.ARABIC_LOW_CONTENT_RATIO


# --- from test_rfc_recovery.py ---

# ---------------------------------------------------------------------------
# D0 fixtures: dynamic converter-subprocess timeout wiring
# ---------------------------------------------------------------------------


class _RecordingTimeout:
    """Stand-in for `asyncio.timeout` that records every `seconds` value it
    is called with, then behaves as a real no-op async context manager so
    the wrapped `await` still runs to completion."""

    def __init__(self, sink: list):
        self.sink = sink

    def __call__(self, seconds):
        self.sink.append(seconds)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def _fake_proc(handshake: dict | None, result: dict, returncode: int = 0):
    proc = MagicMock()
    proc.stdout = MagicMock()
    if handshake is not None:
        proc.stdout.readline = AsyncMock(return_value=(json.dumps(handshake) + "\n").encode())
    else:
        proc.stdout.readline = AsyncMock(return_value=b"")
    stdout = json.dumps(result).encode()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = returncode
    return proc


class TestDynamicTimeoutWiring:
    """Property 1: effective_timeout = max(CHILD_TIMEOUT, chunked_docling_timeout_s(N))
    on a Docling route; CHILD_TIMEOUT unconditionally on a non-Docling route."""

    async def test_docling_route_dynamic_timeout_exceeds_child_timeout(self):
        # Discriminating case: pick a chunk_count whose dynamic timeout is
        # STRICTLY GREATER than CHILD_TIMEOUT, so this test fails if the D0
        # wiring is removed (a bare CHILD_TIMEOUT fallback would land 3600,
        # not chunked_docling_timeout_s(3) = 4800).
        assert chunked_docling_timeout_s(3) > CHILD_TIMEOUT
        handshake = {"handshake": True, "chunk_count": 3, "is_docling_route": True}
        result = {"ok": True, "doc_id": "d1b"}
        proc = _fake_proc(handshake, result)
        sink: list = []
        with (
            patch(
                "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch("pageindex_mcp.worker.subprocess_mgr.asyncio.timeout", _RecordingTimeout(sink)),
        ):
            await _run_converter_subprocess("/tmp/bigger.pdf")
        expected = chunked_docling_timeout_s(3)
        assert expected - 5 <= sink[1] <= expected
        assert sink[1] > CHILD_TIMEOUT

    async def test_non_docling_route_falls_back_to_child_timeout_unconditionally(self):
        handshake = {"handshake": True, "chunk_count": 5, "is_docling_route": False}
        result = {"ok": True, "doc_id": "d2"}
        proc = _fake_proc(handshake, result)
        sink: list = []
        with (
            patch(
                "pageindex_mcp.worker.subprocess_mgr.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ),
            patch("pageindex_mcp.worker.subprocess_mgr.asyncio.timeout", _RecordingTimeout(sink)),
        ):
            await _run_converter_subprocess("/tmp/small.pdf")
        assert CHILD_TIMEOUT - 5 <= sink[1] <= CHILD_TIMEOUT


class TestProbeConversionRoute:
    """`probe_conversion_route` is what the converter child calls to build
    the startup handshake worker.py reads -- covering it here keeps the
    handshake's producer and consumer tested against the same contract."""

    def test_non_pdf_input_reports_non_docling(self):
        assert probe_conversion_route("notes.txt") == (1, False, None)

    def test_pymupdf_failure_reports_non_docling(self):
        # `converters.probe_conversion_route` does a function-local
        # `import fitz`, so `fitz.open` is the patch seam.
        with patch("fitz.open", side_effect=RuntimeError("bad pdf")):
            assert converters.probe_conversion_route("broken.pdf")[:2] == (1, False)


# ---------------------------------------------------------------------------
# D1: Arabic structural heading injection
# ---------------------------------------------------------------------------

# Mirrors scanned-OCR output for a continuous Arabic legal document: no blank
# lines separate consecutive مادة articles, and one article title runs past
# the old 60-char limit (66-76+ chars is the RFC's own observed range).
_CONTINUOUS_OCR_DOC = (
    "الباب الأول أحكام عامة\n"
    "مادة 1\n"
    "يسري هذا القانون على جميع العاملين في الدولة.\n"
    "مادة 2\n"
    "تعريفات هذا القانون كما يلي فيما يتعلق بأحكامه.\n"
    "مادة (3) نطاق التطبيق والأحكام الاستثنائية الخاصة بهذا القانون وتفسيره\n"
    "نص هذه المادة يوضح نطاق التطبيق بالتفصيل.\n"
)


class TestCharLimitRaisedTo100:
    @pytest.fixture(autouse=True)
    def _disable_density_guard(self, monkeypatch):
        import pageindex_mcp.converters.headings as _h
        monkeypatch.setattr(_h, "_AR_HEADING_MIN_CONTENT_CHARS", 0)

    def test_75_char_marker_title_line_is_promoted(self):
        title = "المادة (3) نطاق التطبيق والأحكام الاستثنائية الخاصة بهذا القانون كاملة"
        assert 60 < len(title) <= 100
        md = f"نص سابق.\n\n{title}\nنص لاحق.\n"
        result = _inject_arabic_structural_headings(md)
        assert any(line.startswith("#") and title in line for line in result.splitlines())

    def test_60_char_boundary_still_promoted(self):
        title = "مادة " + ("ن" * 55)  # just over the OLD 60-char cutoff
        md = f"نص سابق.\n\n{title}\nنص لاحق.\n"
        result = _inject_arabic_structural_headings(md)
        assert any(line.startswith("#") and title in line for line in result.splitlines())


# ---------------------------------------------------------------------------
# D2: Arabic Presentation-Forms garble detection
# ---------------------------------------------------------------------------

# Logical-order Arabic letters (U+0600-06FF) vs. Arabic Presentation-Forms
# glyphs (U+FB50-FDFF / U+FE70-FEFF) -- both count as "Arabic-range" for the
# D2 ratio, only the second set is presentation-form variants. Four distinct
# code points per set (rather than one repeated) keeps every constructed
# blob under the PRE-EXISTING, unrelated 30% single-token-repetition garble
# check (D7/RFC-013) so these tests isolate the D2 presentation-forms ratio
# check specifically.
_LOGICAL_LETTERS = ["ا", "ب", "ت", "ث"]
_PRESENTATION_FINAL_FORMS = ["ﺎ", "ﺐ", "ﺖ", "ﺚ"]


def _blob(n_presentation: int, n_logical: int) -> str:
    """Space-separated so the blob has multiple tokens, cycling through
    several distinct code points per category so no single token exceeds
    the unrelated repetition-ratio check's 30% threshold. Characters are
    grouped into 3-char tokens (not single-char tokens) so the blob also
    stays clear of the unrelated D2/RFC-033 single-letter Arabic fragment
    check -- the char-level presentation/logical ratio this test isolates
    is unaffected by token grouping."""

    def _grouped(chars: list[str]) -> list[str]:
        return ["".join(chars[i : i + 3]) for i in range(0, len(chars), 3)]

    pres = _grouped([_PRESENTATION_FINAL_FORMS[i % 4] for i in range(n_presentation)])
    logi = _grouped([_LOGICAL_LETTERS[i % 4] for i in range(n_logical)])
    return " ".join(pres + logi)


class TestPresentationFormsGarbleDetection:
    def test_93_percent_presentation_forms_is_garbled(self):
        # Mirrors huquq-al-insan's 93.6% presentation-forms ratio.
        assert check_garble(_blob(93, 7), expected_script=None, profile=BULK_PROFILE) is True

    def test_exactly_at_threshold_does_not_trigger(self):
        # RFC-028: ratio must EXCEED 0.50, not merely reach it.
        assert check_garble(_blob(50, 50), expected_script=None, profile=BULK_PROFILE) is False


# ---------------------------------------------------------------------------
# D3: RTL-reversal vocabulary + morphology detection
# ---------------------------------------------------------------------------

# Governance/legal sentence built from the RFC-028 D3 vocabulary additions
# (siyasat-hawkama gap: specialized governance terms, not general-purpose
# common words).
_GOV_LOGICAL = "حوكمة البيانات وسياسة الإدارة والتنظيم في القرار الصادر عن الوزارة"

# Mirrors the RFC-027 `_VISUAL_LINE` construction: the whole logical string
# reversed at the character level, simulating OCR/Docling-emitted visual-order
# text -- individual "words" no longer match the vocabulary set.
_GOV_VISUAL = _GOV_LOGICAL[::-1]

# RFC-034 D7: presentation-form glyphs decompose to base Arabic under NFKC
# before `_word_has_reversed_morphology` runs, so the morphological reversal
# signal is now Joining_Type-based (see `_arabic_word_joins`) rather than a
# presentation-form check. A character-reversed base-Arabic word (like a
# genuine visual-order OCR/Docling artifact) is the fixture that exercises it.
_REVERSED_WORD = "رارق"  # "قرار" (decision) reversed at the character level

# Correctly-ordered Arabic with zero `_AR_COMMON_WORDS`/`_AR_DEFINITE_RE`
# matches (country names -- mirrors RFC-027's `_ZERO_SCORE_TEXT`) and no
# presentation-forms shaping, so neither signal should false-positive.
_ZERO_SCORE_LOGICAL_TEXT = "قطر مصر سوريا لبنان تونس كندا اسبانيا دولة عربية"


def _tree_from_lines(lines: list[str]) -> list:
    return [
        {
            "title": "الباب الأول",
            "text": "",
            "start_index": 0,
            "nodes": [
                {"title": f"المادة {i + 1}", "text": line, "start_index": i + 1, "nodes": []}
                for i, line in enumerate(lines)
            ],
        }
    ]


class TestMorphologicalReversalCheck:
    def test_character_reversed_word_flagged_reversed(self):
        assert _word_has_reversed_morphology(_REVERSED_WORD) is True

    def test_plain_logical_word_not_flagged(self):
        assert _word_has_reversed_morphology("قرار") is False


# ---------------------------------------------------------------------------
# D4: OCR retry keep-best-content logic
# ---------------------------------------------------------------------------

# Mirrors al-qarar al-tanzimi: pre-retry text-layer extraction at 230 chars,
# retry's force_full_page_ocr on the same underlying (PUA-encoded) defect
# produces even less content (123 chars) -- the retry must not win.
_PRE_RETRY_TEXT = "أ" * 230
_RETRY_REGRESSED_TEXT = "أ" * 123
_RETRY_IMPROVED_TEXT = "أ" * 400

_GARBLED_TEXT = "" * 200  # U+E000 Private Use Area chars trip _is_garbled_blob
# D10a (RFC-041) activates the Arabic garble-detection PF fallback path;
# Arabic text now fires the presentation_forms prong, making it unsuitable
# as a "clean" baseline.  Use Latin prose instead.
_CLEAN_TEXT = "The quick brown fox jumps over the lazy dog near the river bank. " * 3


def _structure(text: str) -> list:
    return [{"title": "root", "text": text, "start_index": 0, "nodes": []}]


def _keep_best(
    pre_retry_structure: list,
    post_retry_structure: list,
    post_retry_ok: bool,
    expected_script: str | None = None,
) -> tuple[list, bool]:
    """Mirrors client.py's RFC-028 D4 keep-best block (~lines 1049-1080):
    compares post-retry char count against the pre-retry snapshot and decides
    whether the retry result replaces the pre-retry result. Returns
    ``(winning_structure, retry_won)``."""
    pre_retry_chars = len(_flatten_tree_text(pre_retry_structure))
    post_retry_chars = len(_flatten_tree_text(post_retry_structure))
    if post_retry_chars < pre_retry_chars:
        retry_wins = False
    elif post_retry_chars == pre_retry_chars:
        retry_wins = post_retry_ok or (
            check_garble(
                _flatten_tree_text(pre_retry_structure),
                expected_script=expected_script,
                profile=BULK_PROFILE,
            )
            and not check_garble(
                _flatten_tree_text(post_retry_structure),
                expected_script=expected_script,
                profile=BULK_PROFILE,
            )
        )
    else:
        retry_wins = True
    return (post_retry_structure if retry_wins else pre_retry_structure), retry_wins


class TestNearTieGarbleTieBreak:
    def test_equal_chars_pre_garbled_post_clean_retry_wins(self):
        # Equal char count, still-not-ok retry, but pre-retry is garbled and
        # post-retry is clean -- the non-garbled result should win the tie.
        pre = _GARBLED_TEXT
        post = _CLEAN_TEXT + "أ" * (len(_GARBLED_TEXT) - len(_CLEAN_TEXT))
        assert len(pre) == len(post)
        winner, retry_won = _keep_best(_structure(pre), _structure(post), post_retry_ok=False)
        assert retry_won is True
        assert winner == _structure(post)

    def test_equal_chars_pre_clean_post_garbled_pre_retry_wins(self):
        # Inverse: pre-retry clean, post-retry garbled at equal length --
        # pre-retry must win, not the (unconditionally overwritten) retry.
        pre = _CLEAN_TEXT + "أ" * (len(_GARBLED_TEXT) - len(_CLEAN_TEXT))
        post = _GARBLED_TEXT
        assert len(pre) == len(post)
        winner, retry_won = _keep_best(_structure(pre), _structure(post), post_retry_ok=False)
        assert retry_won is False
        assert winner == _structure(pre)


# ---------------------------------------------------------------------------
# D5: picture-OCR language derivation + dedup splicing
# ---------------------------------------------------------------------------

# Ward-597's representative Docling markdown export: near-empty/all-digit, so
# `detect_ocr_langs(md)` alone falls through to ['eng'] -- verified in the
# RFC's own root-cause investigation.
_WARD_597_MD_SAMPLE = "651001429 6 1 mo/2025/597 5/8/2025 51001429"

# Arabic filename -- the escalation-site union pattern (client.py) detects
# script from the filename even when the export carries no usable signal.
_ARABIC_FILENAME = "قرار-597.pdf"


class TestLanguageDetectionSourceIsFilenameUnionedWithMd:
    def test_md_alone_falls_through_to_english(self):
        assert detect_ocr_langs(_WARD_597_MD_SAMPLE) == ["eng"]

    def test_filename_alone_detects_arabic(self):
        assert "ara" in detect_ocr_langs(_ARABIC_FILENAME)


# ---------------------------------------------------------------------------
# D7: Roman-numeral oversized-leaf ordinal splitting
# ---------------------------------------------------------------------------

_WORDS = [
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
    "kilo",
    "lima",
    "mike",
    "november",
    "oscar",
    "papa",
    "quebec",
    "romeo",
    "sierra",
    "tango",
    "uniform",
    "victor",
    "whiskey",
]


def _text_of_length(n: int) -> str:
    if n <= 0:
        return ""
    words = []
    total = 0
    i = 0
    while total < n:
        w = _WORDS[i % len(_WORDS)]
        words.append(w)
        total += len(w) + 1
        i += 1
    return (" ".join(words) + " ")[:n]


def _roman(n: int) -> str:
    vals = [
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ]
    out = []
    for v, sym in vals:
        while n >= v:
            out.append(sym)
            n -= v
    return "".join(out)


class TestRomanNumeralMatching:
    def test_i_ii_iii_all_match(self):
        text = "I. went there.\nII. did that.\nIII. said so.\n"
        matches = list(_OVERSIZED_ORDINAL_RE.finditer(text))
        romans = [m.group("roman") for m in matches if m.group("roman") is not None]
        assert romans == ["I", "II", "III"]

    def test_roman_marker_ordinal_value(self):
        m1 = _OVERSIZED_ORDINAL_RE.search("I. ")
        m2 = _OVERSIZED_ORDINAL_RE.search("II. ")
        m3 = _OVERSIZED_ORDINAL_RE.search("III. ")
        assert _ordinal_value(m1) == (1,)
        assert _ordinal_value(m2) == (2,)
        assert _ordinal_value(m3) == (3,)


class TestMinimumTwoMatchesGuard:
    def test_single_incidental_roman_marker_is_dropped_no_split(self):
        """A single 'I. went to the store' occurrence is prose, not a
        heading sequence -- must not trigger a split."""
        text = (
            f"I. went to the store and {_text_of_length(3000)}\n\n"
            f"{_text_of_length(3000)}\n\n"
            f"{_text_of_length(3000)}"
        )
        tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
        split_oversized_leaf_nodes(
            tree, max_chars=50000, min_segments=3, _tree_ratio=0.1, _tree_total=len(text) * 10
        )
        assert tree[0]["nodes"] == []

    def test_two_roman_markers_are_sufficient_to_split(self):
        """>=2 Roman-numeral matches in the same leaf clear the guard and
        feed the split decision."""
        text = f"I. {_text_of_length(3000)}\nII. {_text_of_length(3000)}"
        tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
        split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=2)
        assert len(tree[0]["nodes"]) == 2
        assert tree[0]["nodes"][0]["text"].startswith("I.")
        assert tree[0]["nodes"][1]["text"].startswith("II.")


class TestHaftpflichtDeepFixture:
    """Reproduces Haftpflicht-Besondere-Bedingungen's structure: a depth-2
    Article node whose oversized leaf text is subdivided into 27
    Roman-numeral sub-clauses (I through XXVII), each itself long enough to
    need no further splitting. Asserts the tree gains a third level (depth
    2 -> 3+) via the recursive `split_oversized_leaf_nodes` call."""

    def test_27_roman_subclauses_split_into_third_level(self):
        clause_text = _text_of_length(2000)
        body = "\n".join(f"{_roman(i)}. {clause_text}" for i in range(1, 28))
        article_node = {
            "node_id": "article-9",
            "title": "Article 9",
            "text": body,
            "nodes": [],
        }
        tree = [
            {
                "node_id": "root",
                "title": "Haftpflicht-Besondere-Bedingungen",
                "text": "",
                "nodes": [article_node],
            }
        ]
        split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)

        # depth 1 (root) -> depth 2 (article_node, unchanged position) ->
        # depth 3 (27 Roman sub-clause children).
        assert tree[0]["nodes"][0] is article_node
        assert len(article_node["nodes"]) == 27
        assert article_node["nodes"][0]["text"].startswith("I.")
        assert article_node["nodes"][26]["text"].startswith("XXVII.")
        for idx, child in enumerate(article_node["nodes"], start=1):
            assert child["text"].startswith(f"{_roman(idx)}.")

    def test_root_still_at_depth_one_no_regression(self):
        """Non-regression: the root node itself (with no oversized text)
        is left untouched -- only the oversized leaf gains children."""
        clause_text = _text_of_length(2000)
        body = "\n".join(f"{_roman(i)}. {clause_text}" for i in range(1, 28))
        article_node = {
            "node_id": "article-9",
            "title": "Article 9",
            "text": body,
            "nodes": [],
        }
        tree = [
            {
                "node_id": "root",
                "title": "Haftpflicht-Besondere-Bedingungen",
                "text": "",
                "nodes": [article_node],
            }
        ]
        split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
        assert tree[0]["node_id"] == "root"
        assert tree[0]["text"] == ""


# ===========================================================================
# Zone: OCR Recovery Cascade and Kill-Switch Conflation
# ===========================================================================


class TestKillSwitchDeconflation:
    """Contract: _recover_low_content_ocr gates on ocr_escalation_low_content
    independently from ocr_escalation_garble.  Regression guard for the
    kill-switch conflation bug where disabling garble escalation silently
    disabled low-content recovery."""

    @pytest.fixture(autouse=True)
    def _restore_cfg(self):
        yield
        from pageindex_mcp.config import reset_pipeline_config
        reset_pipeline_config()

    def _patch_config(self, monkeypatch, *, garble: bool, low_content: bool):
        """Replace pipeline_config with one where the two flags are set independently."""
        import dataclasses as dc
        from pageindex_mcp.config import pipeline_config as _orig, reset_pipeline_config
        import pageindex_mcp.client.recovery as recovery_mod

        new_cfg = dc.replace(_orig, ocr_escalation_garble=garble, ocr_escalation_low_content=low_content)
        monkeypatch.setattr(recovery_mod, "pipeline_config", new_cfg)
        return new_cfg

    def _make_low_content_state(self) -> ExtractionState:
        """State eligible for low-content OCR recovery (ok=False, NODE_COUNT_LOW, low chars)."""
        return ExtractionState(
            result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 10, "nodes": []}]},
            ok=False,
            reason="node_count<3",
            gate_result=TreeGateResult(
                ok=False,
                defect=TreeDefect.NODE_COUNT_LOW,
                all_defects=frozenset({TreeDefect.NODE_COUNT_LOW}),
            ),
            first_defect=TreeDefect.NODE_COUNT_LOW,
            route=Route.FLAT,
            md_content="# low",
            tmp_md_path=None,
            pic_results=[],
            used_converter="docling",
            total_chars=10,
            extraction_stages_captured=[],
        )

    @pytest.mark.asyncio
    async def test_garble_true_low_content_false_skips_low_content_recovery(self, monkeypatch):
        """When garble=True but low_content=False, _recover_low_content_ocr must skip."""
        from pageindex_mcp.client.recovery import RecoveryMixin

        self._patch_config(monkeypatch, garble=True, low_content=False)
        state = self._make_low_content_state()
        mixin = RecoveryMixin()
        # _recover_low_content_ocr checks pipeline_config.ocr_escalation_low_content early
        # and returns without calling _execute_ocr_retry
        mixin._execute_ocr_retry = AsyncMock(side_effect=AssertionError("should not be called"))
        await mixin._recover_low_content_ocr(state, "/f.pdf", "f.pdf", ".pdf", None)
        # State unchanged -- no OCR retry attempted
        assert state.total_chars == 10

    @pytest.mark.asyncio
    async def test_garble_false_low_content_true_runs_low_content_recovery(self, monkeypatch):
        """When garble=False but low_content=True, _recover_low_content_ocr must proceed."""
        from pageindex_mcp.client.recovery import RecoveryMixin

        self._patch_config(monkeypatch, garble=False, low_content=True)
        state = self._make_low_content_state()
        mixin = RecoveryMixin()
        called = []
        async def fake_execute(*a, **kw):
            called.append(True)
            return False
        mixin._execute_ocr_retry = fake_execute
        await mixin._recover_low_content_ocr(state, "/f.pdf", "f.pdf", ".pdf", None)
        assert len(called) == 1, "low-content recovery should have fired"

    @pytest.mark.asyncio
    async def test_both_true_runs_independently(self, monkeypatch):
        """When both flags are True, _recover_low_content_ocr runs (independent of garble)."""
        from pageindex_mcp.client.recovery import RecoveryMixin

        self._patch_config(monkeypatch, garble=True, low_content=True)
        state = self._make_low_content_state()
        mixin = RecoveryMixin()
        called = []
        async def fake_execute(*a, **kw):
            called.append(True)
            return False
        mixin._execute_ocr_retry = fake_execute
        await mixin._recover_low_content_ocr(state, "/f.pdf", "f.pdf", ".pdf", None)
        assert len(called) == 1

    def test_regression_disable_garble_does_not_disable_low_content(self, monkeypatch):
        """Regression guard: the old code gated low-content recovery on
        ocr_escalation_garble.  The new code uses ocr_escalation_low_content.
        Verify by inspecting the source that _recover_low_content_ocr does NOT
        reference ocr_escalation_garble."""
        import inspect
        from pageindex_mcp.client.recovery import RecoveryMixin

        source = inspect.getsource(RecoveryMixin._recover_low_content_ocr)
        assert "ocr_escalation_garble" not in source, (
            "_recover_low_content_ocr must not gate on ocr_escalation_garble "
            "(kill-switch conflation regression)"
        )
        assert "ocr_escalation_low_content" in source


class TestIntegrationRecoveryLoopMultiDefect:
    """Integration: full recovery loop with NODE_COUNT_LOW as first_defect and
    GARBLING as secondary defect fires both low-content and garble recovery."""

    def test_both_recoveries_eligible_when_co_fired(self):
        """When NODE_COUNT_LOW + GARBLING co-fire, both _eligible_low_content
        and _eligible_garble return True."""
        from pageindex_mcp.helpers.gates import _eligible_garble, _eligible_low_content

        state = ExtractionState(
            result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 10, "nodes": []}]},
            ok=False,
            reason="node_count<3",
            gate_result=TreeGateResult(
                ok=False,
                defect=TreeDefect.NODE_COUNT_LOW,
                all_defects=frozenset({TreeDefect.NODE_COUNT_LOW, TreeDefect.GARBLING}),
            ),
            first_defect=TreeDefect.NODE_COUNT_LOW,
            route=Route.FLAT,
            md_content="# test",
            tmp_md_path=None,
            pic_results=[],
            used_converter="docling",
            total_chars=10,
            extraction_stages_captured=[],
        )
        assert _eligible_low_content(state), "NODE_COUNT_LOW must make low-content eligible"
        assert _eligible_garble(state), "GARBLING secondary must make garble eligible"

    def test_gate_specs_cover_both_recovery_chains(self):
        """NODE_COUNT_LOW gate has low-content + image-dominant recovery;
        GARBLING gate has garble + VLM recovery.  When both co-fire, the
        recovery loop visits both gate specs' recovery_fns."""
        gates_by_defect = {g.defect: g for g in GATES}
        ncl_gate = gates_by_defect[TreeDefect.NODE_COUNT_LOW]
        garble_gate = gates_by_defect[TreeDefect.GARBLING]
        # Each has its own recovery chain
        assert "_recover_low_content_ocr" in ncl_gate.recovery_fns
        assert "_recover_garble_ocr" in garble_gate.recovery_fns
        # They are independent recovery chains
        assert set(ncl_gate.recovery_fns) != set(garble_gate.recovery_fns)


# ===========================================================================
# D4: Recovery dispatch cross-tuple dedup (Property 4)
# ===========================================================================


class TestRecoveryDispatchCrossTupleDedup:
    """RFC-041 D4 — Property 4: Recovery Dedup Idempotency.

    When multiple gate tuples share a recovery method name, the dispatch
    loop must execute that method exactly once across all tuples.
    """

    @pytest.mark.asyncio
    async def test_cofiring_defects_single_execution(self, monkeypatch):
        """NODE_COUNT_LOW + DEPTH_LOW both map to _recover_image_dominant_ocr.
        The method must execute exactly once."""
        from pageindex_mcp.client.indexer import CustomPageIndexClient
        from pageindex_mcp.helpers.gates import GATES

        ncl_gate = next(g for g in GATES if g.defect == TreeDefect.NODE_COUNT_LOW)
        depth_gate = next(g for g in GATES if g.defect == TreeDefect.DEPTH_LOW)
        assert "_recover_image_dominant_ocr" in ncl_gate.recovery_fns
        assert "_recover_image_dominant_ocr" in depth_gate.recovery_fns

        state = ExtractionState(
            result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 10, "nodes": []}]},
            ok=False,
            reason="node_count<3",
            gate_result=TreeGateResult(
                ok=False,
                defect=TreeDefect.NODE_COUNT_LOW,
                all_defects=frozenset({TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW}),
            ),
            first_defect=TreeDefect.NODE_COUNT_LOW,
            route=Route.FLAT,
            md_content="# test\n<!-- image -->\n<!-- image -->\n<!-- image -->",
            tmp_md_path=None,
            pic_results=[],
            used_converter="docling",
            total_chars=10,
            extraction_stages_captured=[],
        )

        call_counts: dict[str, int] = {}

        async def _tracking_method(name):
            async def _impl(self_inner, *args, **kwargs):
                call_counts[name] = call_counts.get(name, 0) + 1
            return _impl

        client = CustomPageIndexClient.__new__(CustomPageIndexClient)

        for gate in GATES:
            for fn_name in gate.recovery_fns:
                monkeypatch.setattr(
                    CustomPageIndexClient,
                    fn_name,
                    await _tracking_method(fn_name),
                )

        monkeypatch.setattr(
            "pageindex_mcp.helpers.gates._eligible_low_content",
            lambda s: True,
        )
        monkeypatch.setattr(
            "pageindex_mcp.helpers.gates._eligible_image_dominant",
            lambda s: True,
        )

        from pageindex_mcp.helpers import _flatten_tree_text
        from pageindex_mcp.script import ScriptContext

        _fired_methods: set[str] = set()
        for _gate in GATES:
            if not _gate.recovery_fns:
                continue
            if _gate.recovery_eligible is None or not _gate.recovery_eligible(state):
                continue
            for _fn_name in _gate.recovery_fns:
                if _fn_name in _fired_methods:
                    continue
                _fired_methods.add(_fn_name)
                await getattr(client, _fn_name)(
                    state, "/tmp/test.pdf", "test.pdf", ".pdf", None,
                    script_context=None,
                )

        assert call_counts.get("_recover_image_dominant_ocr", 0) == 1, (
            f"_recover_image_dominant_ocr should execute exactly once, "
            f"got {call_counts.get('_recover_image_dominant_ocr', 0)}"
        )

    @pytest.mark.asyncio
    async def test_full_page_already_applied_skips_image_dominant(self):
        """When full_page_already_applied is True, _recover_image_dominant_ocr
        must skip re-execution."""
        state = ExtractionState(
            result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 10, "nodes": []}]},
            ok=False,
            reason="node_count<3",
            gate_result=None,
            first_defect=TreeDefect.NODE_COUNT_LOW,
            route=Route.FLAT,
            md_content="# test\n<!-- image -->\n<!-- image -->\n<!-- image -->",
            tmp_md_path=None,
            pic_results=[],
            used_converter="docling",
            total_chars=10,
            extraction_stages_captured=[],
            full_page_already_applied=True,
        )
        from pageindex_mcp.client.recovery import RecoveryMixin

        mixin = RecoveryMixin.__new__(RecoveryMixin)
        result = await mixin._recover_image_dominant_ocr(
            state, "/tmp/test.pdf", "test.pdf", ".pdf", None,
        )
        assert result is None


# ===========================================================================
# D4: VLM fallback single tesseract block (Property 4)
# ===========================================================================


class TestVLMFallbackSingleTesseractBlock:
    """RFC-041 D4 — VLM fallback with tesseract raster recovery uses
    a single consolidated block instead of three identical copies."""

    @pytest.mark.asyncio
    async def test_vlm_zdr_compliance_fires_single_tesseract_block(self, monkeypatch):
        """ZDRComplianceError path triggers the consolidated tesseract
        fallback block exactly once."""
        import pageindex_mcp.client.recovery as recovery_mod
        from pageindex_mcp.client.recovery import RecoveryMixin
        from pageindex_mcp.config import ZDRComplianceError

        state = _make_state(
            ok=False,
            first_defect=TreeDefect.GARBLING,
        )
        monkeypatch.setattr(
            "pageindex_mcp.client.recovery.settings",
            MagicMock(vlm_fallback=True, vlm_model="test-model"),
        )

        import dataclasses as dc
        from pageindex_mcp.config import pipeline_config as _orig
        new_cfg = dc.replace(_orig, vlm_tesseract_fallback_enabled=True)
        monkeypatch.setattr(recovery_mod, "pipeline_config", new_cfg)

        async def _vlm_raise(*a, **kw):
            raise ZDRComplianceError("test")
        monkeypatch.setattr(
            "pageindex_mcp.converters.vlm_extract_markdown",
            _vlm_raise,
        )

        tesseract_call_count = 0

        async def _mock_tesseract(*a, **kw):
            nonlocal tesseract_call_count
            tesseract_call_count += 1
            return "# recovered"

        from pageindex_mcp.client import images as images_mod
        monkeypatch.setattr(
            images_mod,
            "_attempt_tesseract_raster_recovery",
            _mock_tesseract,
        )

        mixin = RecoveryMixin.__new__(RecoveryMixin)
        await mixin._recover_vlm_fallback(
            state, "/tmp/test.pdf", "test.pdf", ".pdf", None,
        )

        assert tesseract_call_count == 1
        assert state.md_content == "# recovered"
        assert state.route == Route.FLAT
