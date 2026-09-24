# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Content recovery, RFC recovery, RFC-046 attribution and D4 corrective-retry tests.

Consolidated 2026-09-22: absorbs `test_rfc046_attribution.py`,
`test_d4_corrective_retry.py` and `test_zone3_ocr_recovery.py`. Tests are
grouped by the production function they exercise, not by originating file.
Many-row `@pytest.mark.parametrize` tables were collapsed into single
table-driven tests that collect every failing row and report them together.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pageindex_mcp.converters as converters
from pageindex_mcp.converters import (
    _inject_arabic_structural_headings,
    detect_ocr_langs,
    probe_conversion_route,
)
from pageindex_mcp.helpers import (
    _OVERSIZED_ORDINAL_RE,
    _ReasonPolicy,
    _Unset,
    _ordinal_value,
    _word_has_reversed_morphology,
    arbitrate,
    BULK_PROFILE,
    Candidate,
    ExtractionState,
    finalize_gate_and_route,
    GATES,
    GateSpec,
    RecoveryOutcome,
    Route,
    split_oversized_leaf_nodes,
    TreeDefect,
    TreeGateResult,
    validate_tree,
)
from pageindex_mcp.helpers.types import _defect_from_reason_str
from pageindex_mcp.worker.constants import MAX_EFFECTIVE_TIMEOUT
from pageindex_mcp.worker.timeouts import effective_child_timeout
from pageindex_mcp.worker import (
    CHILD_TIMEOUT,
    _run_converter_subprocess,
)
from tests._garble_compat import check_garble


SRC_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp"


def _src(rel: str) -> str:
    """Read a production source file, for the source-shape guard tests."""
    return (SRC_ROOT / rel).read_text(encoding="utf-8")


_RETRY_POLICIES = frozenset({_ReasonPolicy.RETRY_OCR, _ReasonPolicy.RETRY_RTL})
_GATES_BY_DEFECT: dict[TreeDefect, GateSpec] = {g.defect: g for g in GATES}


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


def _make_garble_state() -> ExtractionState:
    return ExtractionState(
        result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 200, "nodes": []}]},
        ok=False,
        reason="garbled",
        gate_result=TreeGateResult(
            ok=False,
            defect=TreeDefect.NODE_GARBLING,
            all_defects=frozenset({TreeDefect.NODE_GARBLING}),
        ),
        first_defect=TreeDefect.NODE_GARBLING,
        route=Route.REJECT,
        md_content="# garbled content",
        tmp_md_path=None,
        pic_results=[],
        used_converter="pymupdf4llm",
        total_chars=200,
        extraction_stages_captured=[],
    )


def _make_low_content_state(
    all_defects: frozenset[TreeDefect] | None = None,
) -> ExtractionState:
    return ExtractionState(
        result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 10, "nodes": []}]},
        ok=False,
        reason="node_count<3",
        gate_result=TreeGateResult(
            ok=False,
            defect=TreeDefect.NODE_COUNT_LOW,
            all_defects=all_defects or frozenset({TreeDefect.NODE_COUNT_LOW}),
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


# ===========================================================================
# GATES table: recovery wiring, policy, severity ordering, eligibility
# ===========================================================================


class TestGateTable:
    def test_gate_table_invariants(self):
        """Table-driven: every structural invariant of the GATES table in one
        pass, reporting every violating gate rather than the first."""
        failures: list[str] = []

        for g in GATES:
            if g.policy in _RETRY_POLICIES and not g.recovery_fns:
                failures.append(f"{g.defect.value}: retry policy with no recovery_fns")
            if g.recovery_fns and g.recovery_eligible is None:
                failures.append(f"{g.defect.value}: recovery_fns with no recovery_eligible")
            if g.policy == _ReasonPolicy.PERSIST_FAIL and g.recovery_fns:
                failures.append(f"{g.defect.value}: PERSIST_FAIL must never recover")

        persist_fail = [g for g in GATES if g.policy == _ReasonPolicy.PERSIST_FAIL]
        if len(persist_fail) < 3:
            failures.append(f"expected >=3 PERSIST_FAIL gates, got {len(persist_fail)}")

        rtl = _GATES_BY_DEFECT[TreeDefect.RTL_REVERSAL]
        if rtl.policy != _ReasonPolicy.RETRY_RTL:
            failures.append(f"RTL_REVERSAL policy is {rtl.policy}, expected RETRY_RTL")
        if "_recover_rtl_repair" not in rtl.recovery_fns:
            failures.append("RTL_REVERSAL does not wire _recover_rtl_repair")

        severities = [g.severity for g in GATES if g.gate_fn is not None]
        if severities != sorted(severities):
            failures.append(f"active gates not sorted by severity: {severities}")

        # Each defect owns its own recovery chain; NODE_COUNT_LOW and
        # DEPTH_LOW deliberately share _recover_image_dominant_ocr (the
        # cross-tuple dedup case exercised below).
        ncl = _GATES_BY_DEFECT[TreeDefect.NODE_COUNT_LOW]
        depth = _GATES_BY_DEFECT[TreeDefect.DEPTH_LOW]
        garble = _GATES_BY_DEFECT[TreeDefect.GARBLING]
        for gate, fn in (
            (ncl, "_recover_low_content_ocr"),
            (ncl, "_recover_image_dominant_ocr"),
            (depth, "_recover_image_dominant_ocr"),
            (garble, "_recover_garble_ocr"),
        ):
            if fn not in gate.recovery_fns:
                failures.append(f"{gate.defect.value} does not wire {fn}")
        if set(ncl.recovery_fns) == set(garble.recovery_fns):
            failures.append("NODE_COUNT_LOW and GARBLING must be independent chains")

        assert not failures, "GATES table violations:\n  " + "\n  ".join(failures)

    def test_eligibility_predicates_key_off_the_defect(self):
        """A gate's recovery_eligible accepts its own defect and rejects an
        unrelated one; a co-fired secondary defect also makes its gate
        eligible, so a multi-defect document reaches both recovery chains."""
        from pageindex_mcp.helpers.gates import _eligible_garble, _eligible_low_content

        gate = _GATES_BY_DEFECT[TreeDefect.GARBLING]
        assert gate.recovery_eligible(_make_eligibility_state(TreeDefect.GARBLING))
        assert not gate.recovery_eligible(_make_eligibility_state(TreeDefect.RTL_REVERSAL))

        # NODE_COUNT_LOW primary + GARBLING secondary: both chains eligible.
        state = _make_low_content_state(
            all_defects=frozenset({TreeDefect.NODE_COUNT_LOW, TreeDefect.GARBLING})
        )
        assert _eligible_low_content(state), "NODE_COUNT_LOW must make low-content eligible"
        assert _eligible_garble(state), "GARBLING secondary must make garble eligible"


# ===========================================================================
# RecoveryOutcome
# ===========================================================================


class TestRecoveryOutcome:
    def test_frozen_and_defaults_to_unset(self):
        ro = RecoveryOutcome(ok=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ro.ok = False
        empty = RecoveryOutcome()
        for f in dataclasses.fields(empty):
            assert isinstance(getattr(empty, f.name), _Unset)

    def test_apply_semantics(self):
        """`.apply()` writes only the fields that were set: an unset field is
        left alone, an explicit None is written, and a full snapshot reverts
        every field at once (the pre-retry revert path). It also bypasses the
        D3 single-writer guard on the guarded fields."""
        from pageindex_mcp.script import RtlDecision

        # Single field: only `ok` moves, `route` is untouched.
        state = _make_state(ok=False, route=Route.REJECT)
        RecoveryOutcome(ok=True).apply(state)
        assert state.ok is True
        assert state.route == Route.REJECT

        # Explicit None is distinct from unset.
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)
        state = _make_state(gate_result=gate)
        RecoveryOutcome().apply(state)
        assert state.gate_result is gate
        RecoveryOutcome(gate_result=None).apply(state)
        assert state.gate_result is None

        # Guarded fields go through the bypass, not the guard.
        state = _make_state()
        RecoveryOutcome(ok=True, route=Route.FLAT).apply(state)
        assert state.ok is True and state.route == Route.FLAT

        # Full pre-retry snapshot revert.
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
        assert state.md_content == "# pre"
        assert state.used_converter == "docling"
        assert state.tmp_md_path == "/tmp/pre.md"
        assert state.bidi_renorm_applied is True


# ===========================================================================
# validate_tree — dead-gate regression (Hard Rule 5 surface)
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


# ===========================================================================
# _run_converter_subprocess — dynamic child-timeout wiring
# ===========================================================================


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


class _ReadlineFeed:
    """Serves fixed byte chunks one per ``readline()`` call, then returns
    b"" (EOF) forever after. RFC-046 task 12.3: proc.stdout/proc.stderr are
    now drained line-by-line to EOF (not proc.communicate()), so a fixed
    ``AsyncMock(return_value=...)`` (which replays the same bytes forever)
    would spin that read loop forever instead of reaching EOF."""

    def __init__(self, chunks: list[bytes] | None = None):
        self._chunks = list(chunks or [])
        self._idx = 0

    async def readline(self):
        if self._idx >= len(self._chunks):
            return b""
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk

    async def read(self, n: int = -1):
        """The production readers use ``read(n)``, not ``readline()``: a real
        ``asyncio.StreamReader.readline()`` raises ValueError on a line over
        64 KiB, which the child controls. Serves the same scripted chunks.

        An empty scripted chunk means "no handshake line was written", not
        end-of-stream, so it is skipped rather than terminating the feed --
        a real pipe's b"" is permanent EOF and would hide the chunks after it.
        """
        while self._idx < len(self._chunks):
            chunk = self._chunks[self._idx]
            self._idx += 1
            if chunk:
                return chunk
        return b""


def _fake_proc(handshake: dict | None, result: dict, returncode: int = 0):
    proc = MagicMock()
    proc.returncode = returncode
    stdout = json.dumps(result).encode()
    if handshake is not None:
        handshake_line = (json.dumps(handshake) + "\n").encode()
        proc.stdout = _ReadlineFeed([handshake_line, stdout])
    else:
        proc.stdout = _ReadlineFeed([b"", stdout])
    proc.stderr = _ReadlineFeed([])
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.wait = AsyncMock(return_value=returncode)
    return proc


class TestDynamicTimeoutWiring:
    """Property 1: effective_timeout = CHILD_TIMEOUT + chunk_count * PER_CHUNK
    on a chunked Docling route (chunk_count > 1); max(CHILD_TIMEOUT, dynamic)
    for chunk_count <= 1; CHILD_TIMEOUT unconditionally on non-Docling.

    All of it subject to MAX_EFFECTIVE_TIMEOUT, which since 2026-09-18 is
    3600s == CHILD_TIMEOUT -- so on this deployment the chunked branch is
    always capped back to the floor. The formula is still asserted here (via
    ChildTimeout.requested); what the child is *granted* is the capped value.
    """

    async def test_docling_route_dynamic_timeout_is_capped_to_the_ceiling(self):
        from pageindex_mcp.converters.docling_conv import _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S

        chunk_count = 3
        handshake = {"handshake": True, "chunk_count": chunk_count, "is_docling_route": True}
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
        # The formula still asks for the full chunk-proportional budget...
        budget = effective_child_timeout(chunk_count=chunk_count, is_docling_route=True)
        assert (
            budget.requested == CHILD_TIMEOUT + chunk_count * _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S
        )
        assert budget.requested > CHILD_TIMEOUT

        # ...and the cap is what the child actually gets. Under the 60-minute
        # ceiling these differ; if MAX_EFFECTIVE_TIMEOUT is ever raised above
        # the requested value this asserts the uncapped budget instead, with no
        # edit needed.
        assert budget.capped is (budget.requested > MAX_EFFECTIVE_TIMEOUT)
        assert budget.effective - 5 <= sink[1] <= budget.effective

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

    def test_non_docling_inputs_report_non_docling(self):
        # A non-PDF input, and a PDF whose pymupdf probe blows up. The latter
        # does a function-local `import fitz`, so `fitz.open` is the seam.
        assert probe_conversion_route("notes.txt") == (1, False, None, None)
        with patch("fitz.open", side_effect=RuntimeError("bad pdf")):
            assert converters.probe_conversion_route("broken.pdf")[:2] == (1, False)


# ===========================================================================
# D1: Arabic structural heading injection
# ===========================================================================

# Mirrors scanned-OCR output for a continuous Arabic legal document: no blank
# lines separate consecutive مادة articles, and one article title runs past
# the old 60-char limit (66-76+ chars is the RFC's own observed range).


class TestCharLimitRaisedTo100:
    @pytest.fixture(autouse=True)
    def _disable_density_guard(self, monkeypatch):
        import pageindex_mcp.converters.headings as _h

        monkeypatch.setattr(_h, "_AR_HEADING_MIN_CONTENT_CHARS", 0)

    def test_marker_title_lines_up_to_100_chars_are_promoted(self):
        titles = [
            # 75 chars: past the OLD 60-char cutoff, inside the new 100 limit.
            "المادة (3) نطاق التطبيق والأحكام الاستثنائية الخاصة بهذا القانون كاملة",
            # Exactly on the OLD 60-char boundary.
            "مادة " + ("ن" * 55),
        ]
        failures: list[str] = []
        for title in titles:
            assert 60 <= len(title) <= 100, f"fixture drifted out of range: {len(title)}"
            md = f"نص سابق.\n\n{title}\nنص لاحق.\n"
            result = _inject_arabic_structural_headings(md)
            if not any(line.startswith("#") and title in line for line in result.splitlines()):
                failures.append(f"{len(title)} chars: not promoted to a heading")
        assert not failures, "Arabic heading promotion failures:\n  " + "\n  ".join(failures)


# ===========================================================================
# D2: Arabic Presentation-Forms garble detection
# ===========================================================================

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
    def test_presentation_forms_ratio_must_exceed_half(self):
        """Table: 93% presentation forms (huquq-al-insan's observed ratio) is
        garbled; exactly at the 0.50 threshold is NOT (RFC-028: must EXCEED)."""
        cases = [(93, 7, True), (50, 50, False)]
        failures: list[str] = []
        for n_pres, n_logi, expected in cases:
            got = check_garble(_blob(n_pres, n_logi), expected_script=None, profile=BULK_PROFILE)
            if got is not expected:
                failures.append(f"{n_pres}/{n_logi}: expected {expected}, got {got}")
        assert not failures, "presentation-forms ratio failures:\n  " + "\n  ".join(failures)


# ===========================================================================
# D3: RTL-reversal morphology detection
# ===========================================================================

# RFC-034 D7: presentation-form glyphs decompose to base Arabic under NFKC
# before `_word_has_reversed_morphology` runs, so the morphological reversal
# signal is now Joining_Type-based (see `_arabic_word_joins`) rather than a
# presentation-form check. A character-reversed base-Arabic word (like a
# genuine visual-order OCR/Docling artifact) is the fixture that exercises it.


class TestMorphologicalReversalCheck:
    def test_reversed_word_flagged_logical_word_not(self):
        # "قرار" (decision) reversed at the character level, vs. the original.
        assert _word_has_reversed_morphology("رارق") is True
        assert _word_has_reversed_morphology("قرار") is False


# ===========================================================================
# D5: picture-OCR language derivation
# ===========================================================================


class TestLanguageDetectionSourceIsFilenameUnionedWithMd:
    def test_md_falls_through_to_english_filename_detects_arabic(self):
        """Ward-597's Docling markdown export is near-empty/all-digit, so the
        export alone falls through to ['eng']; the escalation site unions in
        the filename, which is where the Arabic signal actually lives."""
        # Representative Docling markdown export for ward-597.
        assert detect_ocr_langs("651001429 6 1 mo/2025/597 5/8/2025 51001429") == ["eng"]
        assert "ara" in detect_ocr_langs("قرار-597.pdf")


# ===========================================================================
# D7: Roman-numeral oversized-leaf ordinal splitting
# ===========================================================================

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
    def test_roman_markers_match_and_carry_their_ordinal_value(self):
        text = "I. went there.\nII. did that.\nIII. said so.\n"
        matches = list(_OVERSIZED_ORDINAL_RE.finditer(text))
        romans = [m.group("roman") for m in matches if m.group("roman") is not None]
        assert romans == ["I", "II", "III"]
        values = [_ordinal_value(_OVERSIZED_ORDINAL_RE.search(f"{r}. ")) for r in romans]
        assert values == [(1,), (2,), (3,)]


class TestMinimumTwoMatchesGuard:
    def test_one_marker_is_prose_two_markers_split(self):
        """A single 'I. went to the store' occurrence is prose, not a heading
        sequence, and must not trigger a split; >=2 matches clear the guard."""
        lone = (
            f"I. went to the store and {_text_of_length(3000)}\n\n"
            f"{_text_of_length(3000)}\n\n"
            f"{_text_of_length(3000)}"
        )
        tree = [{"node_id": "n1", "title": "root", "text": lone, "nodes": []}]
        split_oversized_leaf_nodes(
            tree, max_chars=50000, min_segments=3, _tree_ratio=0.1, _tree_total=len(lone) * 10
        )
        assert tree[0]["nodes"] == [], "a single incidental Roman marker must not split"

        pair = f"I. {_text_of_length(3000)}\nII. {_text_of_length(3000)}"
        tree = [{"node_id": "n1", "title": "root", "text": pair, "nodes": []}]
        split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=2)
        assert len(tree[0]["nodes"]) == 2
        assert tree[0]["nodes"][0]["text"].startswith("I.")
        assert tree[0]["nodes"][1]["text"].startswith("II.")


class TestHaftpflichtDeepFixture:
    """Reproduces Haftpflicht-Besondere-Bedingungen's structure: a depth-2
    Article node whose oversized leaf text is subdivided into 27
    Roman-numeral sub-clauses (I through XXVII), each itself long enough to
    need no further splitting. Asserts the tree gains a third level (depth
    2 -> 3+) via the recursive `split_oversized_leaf_nodes` call, and that
    the non-oversized root is left untouched (non-regression)."""

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
        for idx, child in enumerate(article_node["nodes"], start=1):
            assert child["text"].startswith(f"{_roman(idx)}.")

        # The root itself has no oversized text and must be left alone.
        assert tree[0]["node_id"] == "root"
        assert tree[0]["text"] == ""


# ===========================================================================
# RecoveryMixin._recover_low_content_ocr — kill-switch deconflation
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

    @pytest.mark.asyncio
    async def test_low_content_flag_alone_decides_low_content_recovery(self, monkeypatch):
        """Table over (garble, low_content) -> whether _execute_ocr_retry fires.
        Only `ocr_escalation_low_content` may decide; `ocr_escalation_garble`
        must be irrelevant here."""
        import dataclasses as dc

        import pageindex_mcp.client.recovery as recovery_mod
        from pageindex_mcp.client.recovery import RecoveryMixin
        from pageindex_mcp.config import pipeline_config as _orig

        cases = [
            (True, False, 0),  # garble on, low-content off -> must skip
            (False, True, 1),  # garble off, low-content on -> must fire
            (True, True, 1),  # both on -> fires, independently of garble
        ]
        failures: list[str] = []
        for garble, low_content, expected in cases:
            monkeypatch.setattr(
                recovery_mod,
                "pipeline_config",
                dc.replace(
                    _orig, ocr_escalation_garble=garble, ocr_escalation_low_content=low_content
                ),
            )
            state = _make_low_content_state()
            mixin = RecoveryMixin()
            calls: list[bool] = []

            async def fake_execute(*a, _sink=calls, **kw):
                _sink.append(True)
                return False

            mixin._execute_ocr_retry = fake_execute
            await mixin._recover_low_content_ocr(state, "/f.pdf", "f.pdf", ".pdf", None)
            if len(calls) != expected:
                failures.append(
                    f"garble={garble} low_content={low_content}: "
                    f"expected {expected} OCR retries, got {len(calls)}"
                )
        assert not failures, "kill-switch deconflation failures:\n  " + "\n  ".join(failures)

    def test_low_content_recovery_does_not_read_the_garble_flag(self):
        """Source guard for the regression above: the old code gated
        low-content recovery on ocr_escalation_garble."""
        import inspect

        from pageindex_mcp.client.recovery import RecoveryMixin

        source = inspect.getsource(RecoveryMixin._recover_low_content_ocr)
        assert "ocr_escalation_garble" not in source, (
            "_recover_low_content_ocr must not gate on ocr_escalation_garble "
            "(kill-switch conflation regression)"
        )
        assert "ocr_escalation_low_content" in source


class TestZeroContentRecoveryFlow:
    """RFC-043 D1: locks the zero-content recovery flow as a regression
    guard.  Zero-content documents (total_chars=0, node_count=0) must still
    reach OCR recovery -- ``_eligible_low_content`` gates on flags + defect
    membership only (no char threshold), and the char-floor *skip* guard in
    ``_recover_low_content_ocr`` (``0 >= 300`` = False) correctly lets them
    through rather than blocking them."""

    @pytest.fixture(autouse=True)
    def _restore_cfg(self):
        yield
        from pageindex_mcp.config import reset_pipeline_config

        reset_pipeline_config()

    def _make_zero_content_state(self) -> ExtractionState:
        return ExtractionState(
            result={"structure": []},
            ok=False,
            reason="node_count<3",
            gate_result=TreeGateResult(
                ok=False,
                defect=TreeDefect.NODE_COUNT_LOW,
                all_defects=frozenset({TreeDefect.NODE_COUNT_LOW}),
            ),
            first_defect=TreeDefect.NODE_COUNT_LOW,
            route=Route.FLAT,
            md_content="",
            tmp_md_path=None,
            pic_results=[],
            used_converter="docling",
            total_chars=0,
            extraction_stages_captured=[],
        )

    def test_eligible_low_content_true_for_zero_node_document(self, monkeypatch):
        """_eligible_low_content(state) is True for a zero-content document as
        long as one of the OCR-escalation flags is on, and the char-floor guard
        (`total_chars >= low_content_ocr_char_floor`) is a *skip* guard that
        zero-content documents pass through rather than get blocked by."""
        import dataclasses as dc

        import pageindex_mcp.helpers.gates as gates_mod
        from pageindex_mcp.config import pipeline_config as _orig
        from pageindex_mcp.helpers.gates import _eligible_low_content

        new_cfg = dc.replace(
            _orig, ocr_escalation_low_content=True, image_dominant_ocr_escalation_enabled=False
        )
        monkeypatch.setattr(gates_mod, "pipeline_config", new_cfg)
        state = self._make_zero_content_state()
        assert state.total_chars == 0
        assert _eligible_low_content(state)
        assert not (state.total_chars >= _orig.low_content_ocr_char_floor)

    @pytest.mark.asyncio
    async def test_recover_low_content_ocr_proceeds_with_zero_chars(self, monkeypatch):
        """_recover_low_content_ocr must invoke OCR retry for a document
        with total_chars=0 -- the char-floor guard (0 >= 300 = False) must
        not skip recovery."""
        import dataclasses as dc

        import pageindex_mcp.client.recovery as recovery_mod
        from pageindex_mcp.client.recovery import RecoveryMixin
        from pageindex_mcp.config import pipeline_config as _orig

        new_cfg = dc.replace(_orig, ocr_escalation_low_content=True)
        monkeypatch.setattr(recovery_mod, "pipeline_config", new_cfg)
        state = self._make_zero_content_state()
        mixin = RecoveryMixin()
        called = []

        async def fake_execute(*a, **kw):
            called.append(True)
            return False

        mixin._execute_ocr_retry = fake_execute
        await mixin._recover_low_content_ocr(state, "/f.pdf", "f.pdf", ".pdf", None)
        assert len(called) == 1, "zero-content document must reach _execute_ocr_retry"


# ===========================================================================
# Recovery dispatch: cross-tuple dedup and re-entry guards
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
                    state,
                    "/tmp/test.pdf",
                    "test.pdf",
                    ".pdf",
                    None,
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
            state,
            "/tmp/test.pdf",
            "test.pdf",
            ".pdf",
            None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_vlm_escape_hatch_unblocked_by_full_page_already_applied(self, monkeypatch):
        """RFC-044 D1/Property 1: when full_page_already_applied is True and
        GARBLING fires, _recover_garble_ocr must return early (guard blocks
        redundant OCR), but _recover_vlm_fallback is a distinct strategy and
        must still be called (VLM is the intended escape hatch)."""
        state = _make_state(ok=False, first_defect=TreeDefect.GARBLING)
        state.full_page_already_applied = True
        from pageindex_mcp.client.recovery import RecoveryMixin

        mixin = RecoveryMixin.__new__(RecoveryMixin)
        mixin._execute_ocr_retry = AsyncMock(side_effect=AssertionError("should not be called"))
        await mixin._recover_garble_ocr(state, "/f.pdf", "f.pdf", ".pdf", None)
        mixin._execute_ocr_retry.assert_not_called()

        monkeypatch.setattr(
            "pageindex_mcp.client.recovery.settings",
            MagicMock(vlm_fallback=True, vlm_model="test-model"),
        )
        vlm_called = AsyncMock(return_value="# vlm recovered")
        monkeypatch.setattr(converters, "vlm_extract_markdown", vlm_called)
        mixin._reconvert_and_revalidate = AsyncMock()

        # Keep the test hermetic: the D7 tesseract-raster tail is out of scope
        # here and would otherwise reach real tessdata/rasterization code.
        import dataclasses as dc

        import pageindex_mcp.client.recovery as recovery_mod
        from pageindex_mcp.config import pipeline_config as _orig_cfg

        monkeypatch.setattr(
            recovery_mod,
            "pipeline_config",
            dc.replace(
                _orig_cfg,
                d7_garble_recovery_enabled=False,
                vlm_tesseract_fallback_enabled=False,
            ),
        )

        await mixin._recover_vlm_fallback(state, "/f.pdf", "f.pdf", ".pdf", None)
        vlm_called.assert_called_once()


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
            state,
            "/tmp/test.pdf",
            "test.pdf",
            ".pdf",
            None,
        )

        assert tesseract_call_count == 1
        assert state.md_content == "# recovered"
        assert state.route == Route.FLAT


class TestReEntryGuardEnforcement:
    """RFC-044 D1: _recover_garble_ocr and _recover_low_content_ocr must
    respect state.full_page_already_applied, matching the pre-existing
    guard in _recover_image_dominant_ocr, so a document that already
    received full-page OCR does not trigger a redundant retry.

    The corrective-retry loop is bounded by that single flag; an off-by-one
    here is a cost and latency incident, so the cascade is asserted too."""

    _METHODS = (
        ("_recover_garble_ocr", _make_garble_state),
        ("_recover_low_content_ocr", _make_low_content_state),
    )

    @pytest.mark.asyncio
    async def test_guard_blocks_retry_exactly_when_flag_is_set(self):
        """Table over (method, flag) -> whether _execute_ocr_retry fires.
        Flag True must block; flag False must still fire (no D1 regression)."""
        from pageindex_mcp.client.recovery import RecoveryMixin

        failures: list[str] = []
        for method_name, make_state in self._METHODS:
            for flag, expected_calls in ((True, 0), (False, 1)):
                state = make_state()
                state.full_page_already_applied = flag
                mixin = RecoveryMixin()
                mixin._execute_ocr_retry = AsyncMock(return_value=False)
                await getattr(mixin, method_name)(state, "/f.pdf", "f.pdf", ".pdf", None)
                got = mixin._execute_ocr_retry.call_count
                if got != expected_calls:
                    failures.append(
                        f"{method_name} with full_page_already_applied={flag}: "
                        f"expected {expected_calls} retries, got {got}"
                    )
        assert not failures, "re-entry guard failures:\n  " + "\n  ".join(failures)

    @pytest.mark.asyncio
    async def test_guard_cascades_to_prevent_triple_ocr(self):
        """Guard-cascading test (absorbed from dropped Task 5.2): a first
        recovery that succeeds sets full_page_already_applied=True, and a
        second recovery on the same state respects that flag, preventing a
        third OCR pass."""
        from pageindex_mcp.client.recovery import RecoveryMixin

        state = _make_garble_state()
        mixin = RecoveryMixin()
        mixin._execute_ocr_retry = AsyncMock(return_value=True)

        # First recovery: flag starts False, OCR retry applied, flag flips True.
        await mixin._recover_garble_ocr(state, "/f.pdf", "f.pdf", ".pdf", None)
        assert mixin._execute_ocr_retry.call_count == 1
        assert state.full_page_already_applied is True

        # Second recovery (e.g. low-content dispatched after garble in the
        # same GATES loop pass): must respect the now-True flag and skip.
        await mixin._recover_low_content_ocr(state, "/f.pdf", "f.pdf", ".pdf", None)
        assert mixin._execute_ocr_retry.call_count == 1, (
            "second recovery must not trigger a redundant (third) OCR pass"
        )


# ===========================================================================
# D3: single-writer invariant on ExtractionState
# ===========================================================================


class TestD3GuardedFieldProtection:
    def test_guarded_fields_reject_direct_assignment(self):
        """Table over every guarded field; non-guarded fields stay writable."""
        failures: list[str] = []
        for field in ("route", "ok", "reason", "first_defect", "gate_result"):
            state = _make_state()
            try:
                setattr(state, field, None)
            except AttributeError as exc:
                if "D3 single-writer" not in str(exc):
                    failures.append(f"{field}: wrong AttributeError message: {exc}")
            else:
                failures.append(f"{field}: direct assignment was allowed")
        assert not failures, "D3 single-writer failures:\n  " + "\n  ".join(failures)

        state = _make_state()
        state.md_content = "new content"
        assert state.md_content == "new content"

    def test_finalize_gate_and_route_bypasses_guard(self):
        state = _make_state()
        finalize_gate_and_route(state, TreeGateResult(ok=True, defect=TreeDefect.OK))
        assert state.ok is True
        assert state.route == Route.TREE


class TestD3DefectFromReasonStr:
    def test_reason_string_to_defect_table(self):
        cases = [
            ("", TreeDefect.OK),
            (None, TreeDefect.OK),
            ("garbling", TreeDefect.GARBLING),
            ("garbling(ratio=0.5)", TreeDefect.GARBLING),
        ]
        failures: list[str] = []
        for reason, expected in cases:
            got = _defect_from_reason_str(reason)
            if got is not expected:
                failures.append(f"{reason!r}: expected {expected}, got {got}")
        assert not failures, "reason->defect failures:\n  " + "\n  ".join(failures)

        with pytest.raises(ValueError, match="Unrecognized reason string"):
            _defect_from_reason_str("totally_unknown_reason")


class TestD3ForceRouteOverride:
    def test_force_route_and_force_ok_at_every_recovery_site(self):
        """Table over the force_route/force_ok call shapes plus each real
        recovery call site, asserting the (route, ok) each one produces."""

        def ok_gate():
            return TreeGateResult(ok=True, defect=TreeDefect.OK)

        cases = [
            (
                "force_route_only",
                lambda: _make_state(),
                ok_gate,
                {"force_route": Route.FLAT},
                Route.FLAT,
                True,
            ),
            (
                "force_ok_only",
                lambda: _make_state(),
                ok_gate,
                {"force_ok": False},
                Route.TREE,
                False,
            ),
            (
                "force_route_and_ok",
                lambda: _make_state(),
                ok_gate,
                {"force_route": Route.FLAT, "force_ok": False},
                Route.FLAT,
                False,
            ),
            (
                "rtl_comparison",
                lambda: _make_state(
                    ok=False, route=Route.REJECT, first_defect=TreeDefect.RTL_REVERSAL
                ),
                lambda: TreeGateResult(
                    ok=False, defect=TreeDefect.RTL_REVERSAL, detail="rtl_reversal"
                ),
                {
                    "recovery_method": "rtl_comparison",
                    "recovery_succeeded": True,
                    "force_route": Route.FLAT,
                },
                Route.FLAT,
                False,
            ),
            (
                "vlm_tesseract_raster",
                lambda: _make_state(ok=False, route=Route.REJECT, first_defect=TreeDefect.GARBLING),
                lambda: TreeGateResult(ok=False, defect=TreeDefect.GARBLING, detail="garbling"),
                {
                    "recovery_method": "vlm_tesseract_raster",
                    "recovery_succeeded": True,
                    "force_route": Route.FLAT,
                },
                Route.FLAT,
                False,
            ),
            (
                "flat_prefer_density",
                lambda: _make_state(ok=True, route=Route.TREE),
                ok_gate,
                {
                    "recovery_method": "flat_prefer_density",
                    "recovery_succeeded": True,
                    "force_route": Route.FLAT,
                    "force_ok": False,
                },
                Route.FLAT,
                False,
            ),
            (
                "landscape_reroute",
                lambda: _make_state(ok=True, route=Route.TREE),
                ok_gate,
                {
                    "recovery_method": "landscape_reroute",
                    "recovery_succeeded": True,
                    "force_route": Route.FLAT,
                    "force_ok": False,
                },
                Route.FLAT,
                False,
            ),
        ]

        failures: list[str] = []
        for label, make_state, make_gate, kwargs, exp_route, exp_ok in cases:
            state = make_state()
            finalize_gate_and_route(state, make_gate(), **kwargs)
            if state.route != exp_route or state.ok is not exp_ok:
                failures.append(
                    f"{label}: expected route={exp_route} ok={exp_ok}, "
                    f"got route={state.route} ok={state.ok}"
                )
        assert not failures, "force_route/force_ok failures:\n  " + "\n  ".join(failures)


class TestD3DeprecationWarning:
    def test_legacy_tuple_warns_tree_gate_result_does_not(self):
        import warnings

        state = _make_state()
        with pytest.warns(DeprecationWarning, match="legacy.*tuple"):
            finalize_gate_and_route(state, (True, ""))

        state = _make_state()
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            finalize_gate_and_route(state, TreeGateResult(ok=True, defect=TreeDefect.OK))


# ===========================================================================
# client/recovery.py pure helpers: _repeating_token_density, _keep_best_wins
# (absorbed from tests/test_zone3_ocr_recovery.py)
# ===========================================================================


def _tree(text: str) -> dict:
    return {"structure": [{"node_id": "1", "title": "", "text": text, "nodes": []}]}


class TestRepeatingTokenDensity:
    def test_density_table(self):
        """Table: below the 20-token floor the measure saturates at 1.0; an
        all-identical corpus is 1.0; all-unique is near zero; a 50/50 mix
        lands in between."""
        from pageindex_mcp.client.recovery import _repeating_token_density

        cases = [
            ("short text", "hello world", 1.0, 1.0),
            ("19 tokens (under the floor)", " ".join(f"word{i}" for i in range(19)), 1.0, 1.0),
            ("40 identical tokens", " ".join(["xkjqz"] * 40), 1.0, 1.0),
            ("40 unique tokens", " ".join(f"uniqueword{i}" for i in range(40)), 0.0, 0.1),
            (
                "50/50 mix",
                " ".join(["repeat"] * 20 + [f"unique{i}" for i in range(20)]),
                0.4,
                0.6,
            ),
        ]
        failures: list[str] = []
        for label, text, lo, hi in cases:
            got = _repeating_token_density(text)
            if not (lo <= got <= hi):
                failures.append(f"{label}: expected {lo}..{hi}, got {got}")
        assert not failures, "repeating-token density failures:\n  " + "\n  ".join(failures)


class TestKeepBestWins:
    def test_char_count_cascade(self):
        """Table over the char-count arm of the keep-best cascade: a zero-char
        pre-retry always loses, a shorter post-retry always loses, and a longer
        non-garbled post-retry wins."""
        from pageindex_mcp.client.recovery import _keep_best_wins

        clean_pre = "This is a perfectly ordinary section of legible English prose text."
        clean_post = clean_pre + " And some more legible text added by OCR retry."
        cases = [
            ("zero-char pre, any post", "", 0, "hello world new content", True),
            ("post regresses on chars", "a" * 500, 500, "b" * 100, False),
            ("post adds clean chars", clean_pre, len(clean_pre), clean_post, True),
        ]
        failures: list[str] = []
        for label, pre, pre_chars, post, expected in cases:
            got = _keep_best_wins(
                pre_result=_tree(pre),
                pre_total_chars=pre_chars,
                post_result=_tree(post),
                post_ok=True,
                expected_script=None,
                script_context=None,
                filename="test.pdf",
            )
            if got is not expected:
                failures.append(f"{label}: expected {expected}, got {got}")
        assert not failures, "keep-best cascade failures:\n  " + "\n  ".join(failures)

    def test_rfc045_density_worse_but_post_not_garbled_keeps_retry(self):
        """RFC-045: when pre-retry is garbled and post-retry has higher density
        but is NOT garbled, the density increase is from legitimate content
        repetition — keep the retry."""
        from pageindex_mcp.client.recovery import _keep_best_wins
        from pageindex_mcp.script import ScriptContext

        garbled_latin = " ".join(f"xk{i}qz elas Sie Cys de ABUL Lem oJ oiS" for i in range(25))
        clean_arabic = " ".join(["وزارة الصناعة والتكنولوجيا المتقدمة وزارة الموارد البشرية"] * 25)
        ctx = ScriptContext(
            dominant_script="Arab",
            had_presentation_forms=False,
            source="test",
        )
        result = _keep_best_wins(
            pre_result=_tree(garbled_latin),
            pre_total_chars=len(garbled_latin),
            post_result=_tree(clean_arabic),
            post_ok=True,
            expected_script="Arab",
            script_context=ctx,
            filename="doc6_mohre.pdf",
        )
        assert result is True, (
            "RFC-045: retry with clean Arabic should win over garbled Latin "
            "even when repeating-token density is higher"
        )


# ===========================================================================
# RFC-046 D2: OCR engine + prong attribution
# (absorbed from tests/test_rfc046_attribution.py)
# ===========================================================================


class TestOcrEngineIdentity:
    """D2 / Property 2: an engine identity exists and is threaded as data."""

    def test_engine_enum_shape(self):
        from pageindex_mcp.picture_plane import OcrEngine

        assert {e.value for e in OcrEngine} == {"tesseract", "surya"}
        assert OcrEngine.TESSERACT == "tesseract"
        # Interpolating into a log line or a sidecar must not yield "OcrEngine.X".
        assert f"{OcrEngine.TESSERACT}" == "tesseract"


class TestOcrDecisionCarriesEngine:
    """D2: OcrDecision gains `engine`, defaulted so existing callers are unaffected."""

    def test_engine_field_default_settable_and_frozen(self):
        from pageindex_mcp.picture_plane import OcrDecision, OcrEngine, OcrMode

        assert OcrDecision(mode=OcrMode.NONE).engine is OcrEngine.TESSERACT
        explicit = OcrDecision(mode=OcrMode.FULL_PAGE, engine=OcrEngine.TESSERACT)
        assert explicit.engine is OcrEngine.TESSERACT
        with pytest.raises(dataclasses.FrozenInstanceError):
            explicit.engine = OcrEngine.TESSERACT  # type: ignore[misc]

    def test_every_decision_path_carries_an_engine(self):
        """D2: no branch of decide_ocr_strategy may emit a decision without an
        engine label — including the re-entry-guard short circuit."""
        from pageindex_mcp.picture_plane import OcrEngine, OcrMode, decide_ocr_strategy

        cases = [
            ({"ocr_escalation_enabled": False, "has_image_markers": False}, OcrMode.NONE),
            (
                {
                    "ocr_escalation_enabled": False,
                    "has_image_markers": False,
                    "force_full_page": True,
                },
                OcrMode.FULL_PAGE,
            ),
            ({"ocr_escalation_enabled": True, "has_image_markers": True}, OcrMode.PER_PICTURE),
            (
                {
                    "ocr_escalation_enabled": True,
                    "has_image_markers": True,
                    "full_page_already_applied": True,
                },
                OcrMode.NONE,
            ),
        ]
        failures: list[str] = []
        for kwargs, expected_mode in cases:
            decision = decide_ocr_strategy(**kwargs)
            if decision.mode is not expected_mode:
                failures.append(f"{kwargs}: expected mode {expected_mode}, got {decision.mode}")
            if decision.engine is not OcrEngine.TESSERACT:
                failures.append(f"{kwargs}: no engine attribution ({decision.engine})")
        assert not failures, "decide_ocr_strategy attribution failures:\n  " + "\n  ".join(failures)

    def test_forwarded_params_reach_the_decision(self):
        """Zone-3: garble_status / document_type / ocr_langs are accepted AND
        carried onto the emitted decision (this test used to assert only
        `isinstance(result, OcrDecision)`, which cannot fail)."""
        from pageindex_mcp.picture_plane import OcrEngine, OcrMode, decide_ocr_strategy

        result = decide_ocr_strategy(
            ocr_escalation_enabled=True,
            has_image_markers=True,
            garble_status=True,
            document_type="pdf",
            ocr_langs=["deu", "ara"],
        )
        assert result.mode is OcrMode.PER_PICTURE
        assert result.garble_status is True
        assert result.has_image_markers is True
        assert result.ocr_langs == ["deu", "ara"]
        assert result.engine is OcrEngine.TESSERACT


class TestAttributionCarriers:
    """D2 / R2.3: the carriers that move engine provenance to the sidecar."""

    def test_picture_result_and_extraction_state_carry_an_engine(self):
        from pageindex_mcp.converters.types import PictureResult

        assert "ocr_engine" in PictureResult.__annotations__
        names = {f.name for f in dataclasses.fields(ExtractionState)}
        assert "ocr_engine" in names, "ExtractionState must carry document-level engine"


class TestAllOcrSitesDeclareTheirEngine:
    """D2 / R2.3 / Property 2 — the five OCR invocation sites.

    Prior enumerations consistently found four and missed
    ``_landscape_rasterize_rotate_reextract``, which consults no decision
    function at all and is implicated in the Doc 17 failure. Enumerating them
    by name here means a sixth site added later fails this test rather than
    silently escaping attribution.
    """

    SITES: ClassVar[dict[str, str]] = {
        "converters/pictures.py": "_tesseract_ocr_image",
        "converters/formats.py": "tesseract_ocr_pdf_pages",
        "converters/docling_conv.py": "TesseractCliOcrOptions",
        "client/recovery.py": "_attempt_tesseract_raster_recovery",
        "converters/pipeline.py": "_landscape_rasterize_rotate_reextract",
    }

    def test_every_ocr_site_file_declares_the_engine(self):
        missing = []
        for rel, symbol in self.SITES.items():
            src = _src(rel)
            assert symbol in src, f"{rel}: OCR site {symbol} vanished -- update this test"
            if "OcrEngine" not in src:
                missing.append(rel)
        assert not missing, (
            "these OCR invocation sites do not declare an OcrEngine, so a verdict "
            f"produced through them cannot be attributed: {missing}"
        )


class TestConverterNameIsSourcedNotRestated:
    """D2 / R2.4: ``state.used_converter`` must not be a bare hardcoded literal.

    ``recovery.py`` assigned ``state.used_converter = "docling"`` directly while
    ``pipeline.py`` independently named the same converter in four places. The
    name is now defined once and imported, so the two cannot drift. The OCR
    retry path forces full-page OCR, so it must also record an engine or its
    verdict is unattributable (R2.3).
    """

    def test_recovery_sources_the_converter_name_and_records_the_engine(self):
        from pageindex_mcp.converters.pipeline import DOCLING_CONVERTER_NAME

        assert DOCLING_CONVERTER_NAME == "docling"
        src = _src("client/recovery.py")
        assert 'used_converter = "docling"' not in src, (
            "recovery.py must source the converter name, not restate it (R2.4)"
        )
        assert "state.ocr_engine" in src, "the OCR retry path must record state.ocr_engine (R2.3)"


class TestGarbleProngsSurviveToTheSidecar:
    """D2 / R2.5: `fired_prongs` is computed on every garble evaluation and thrown away.

    `detect_garble` returns a `GarbleReport` naming which of thirteen prongs
    condemned a document, but `TreeSignals.from_tree` wrapped the call in
    `bool(...)` and `_persist_tree_result` writes only `all_defects`. So no
    stored artifact says *why* a document was called garbled.

    That is why Doc 22 cannot be diagnosed from the store: `presentation_forms`
    (a verdict defect, fixed by D5) and `single_letter_fragments` (genuine
    Arabic shaping loss, out of scope) are indistinguishable after the fact,
    and they have opposite fixes.
    """

    def test_tree_signals_carry_the_fired_prongs(self):
        from pageindex_mcp.helpers.tree_validation import TreeSignals

        assert "garble_prongs" in {f.name for f in dataclasses.fields(TreeSignals)}

        clean = TreeSignals.from_tree(
            [{"title": "Introduction", "text": "This is clean English prose. " * 20}]
        )
        assert clean.garbled is False
        assert clean.garble_prongs == frozenset()

        # PUA codepoints are an unambiguous, script-independent garble signal.
        garbled = TreeSignals.from_tree([{"title": "X", "text": "" * 200}])
        assert garbled.garbled is True
        assert garbled.garble_prongs, "a garbled document must name at least one prong"
        assert all(isinstance(p, str) for p in garbled.garble_prongs)


class TestSidecarCarriesAttribution:
    """D2 / R2.5-R2.7: both persistence paths must record engine and prongs.

    `_persist_tree_result` and `_persist_flat_result` did not agree on what
    they recorded. Wave 1 makes both carry the same attribution fields, so a
    corpus diff is explainable regardless of which route a document took.
    """

    def test_both_persistence_paths_record_engine_and_prongs(self):
        src = _src("client/indexer.py")
        missing = [
            key
            for key in (
                'meta["garble_prongs"]',
                'meta["ocr_engine"]',
                'flat_meta["garble_prongs"]',
                'flat_meta["ocr_engine"]',
            )
            if key not in src
        ]
        assert not missing, f"indexer.py persistence paths drop attribution: {missing}"


class TestImagePathRecordsItsEngine:
    """D2 / R2.3 — gap found by the 2026-09-15 smoke test.

    Doc 13 (pie chart) demonstrably ran OCR -- its stored blocks contain
    Latin-transliteration output -- yet no ``ocr_engine`` reached the sidecar,
    because only the *recovery* paths set ``state.ocr_engine``. "No engine in
    the sidecar" therefore meant "no OCR retry ran", not "no OCR ran", which
    would have made the Wave 1 baseline actively misleading.
    """

    def test_ocr_call_sites_attribute_the_engine_at_the_call_site(self):
        # Must be set *at the OCR call site*, not merely mentioned by the
        # persistence code -- otherwise the assertion passes vacuously.
        indexer = _src("client/indexer.py")
        # Find the *primary* _tesseract_ocr_image call (the fallback for the
        # initial image OCR), not the D4 corrective-retry call added by
        # task 6.2.  The primary site is the first non-import occurrence.
        first = indexer.find("_tesseract_ocr_image")
        assert first != -1, "standalone-image OCR call site vanished -- update this test"
        idx = indexer.find("_tesseract_ocr_image", first + 1)
        assert idx != -1, "standalone-image OCR call site vanished -- update this test"
        window = indexer[max(0, idx - 1500) : idx + 500]
        assert "state.ocr_engine" in window, (
            "the standalone-image OCR path runs tesseract (indexer.py:915) and "
            "must record the engine at the call site, or its verdict is unattributable"
        )

        # _attempt_tesseract_raster_recovery (images.py) has no `state`, so
        # attribution is owned by its caller in recovery.py, which sets the
        # engine before invoking it. Assert that, not something images.py
        # cannot do.
        recovery = _src("client/recovery.py")
        r_idx = recovery.find("_attempt_tesseract_raster_recovery(")
        assert r_idx != -1, "raster-recovery call site vanished -- update this test"
        assert "state.ocr_engine" in recovery[max(0, r_idx - 600) : r_idx], (
            "the caller of _attempt_tesseract_raster_recovery must attribute the engine"
        )


# ===========================================================================
# RFC-046 D4: image recovery eligibility + bounded corrective retry
# (absorbed from tests/test_d4_corrective_retry.py)
# ===========================================================================


def _recovery_method_source(method_name: str) -> str:
    """Return the source of a single method from client/recovery.py."""
    text = _src("client/recovery.py")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name:
            return ast.get_source_segment(text, node)
    raise LookupError(f"{method_name} not found in recovery.py")


class TestImageRecoveryEligibility:
    """Task 6.1: recovery methods must accept _IMAGE_EXTS, not only .pdf."""

    def test_every_recovery_method_guards_on_image_exts(self):
        from pageindex_mcp.client.images import _IMAGE_EXTS

        assert _IMAGE_EXTS, "the image-extension set must not be empty"
        methods = [
            "_recover_garble_ocr",
            "_recover_low_content_ocr",
            "_recover_image_dominant_ocr",
            "_recover_rtl_repair",
            "_recover_vlm_fallback",
        ]
        missing = [m for m in methods if "_IMAGE_EXTS" not in _recovery_method_source(m)]
        assert not missing, (
            f"these recovery methods do not reference _IMAGE_EXTS, so image inputs "
            f"are silently skipped: {missing}"
        )
        assert "from .images import _IMAGE_EXTS" in _src("client/recovery.py")

    def test_execute_ocr_retry_has_image_dispatch(self):
        """_execute_ocr_retry must have an image-specific OCR dispatch path."""
        src = _recovery_method_source("_execute_ocr_retry")
        assert "image_to_markdown" in src, (
            "_execute_ocr_retry lacks image_to_markdown dispatch — "
            "image inputs will hit the PDF-only converter path"
        )
        assert "image_tesseract" in src, "_execute_ocr_retry lacks image_tesseract decision choice"


class TestCorrectiveRetry:
    """Task 6.2: bounded detect-correct-retry in the image path."""

    def test_corrective_path_is_wired_through_arbitrate(self):
        src = _src("client/indexer.py")
        assert "d4_corrective_retry" in src, "the d4_corrective_retry decision event is gone"
        assert "arbitrate(" in src, "the corrective path must use arbitrate(), not ad-hoc compare"

    def test_arbitrate_prefers_clean_and_tie_breaks_to_the_original(self):
        """Garbled original vs clean corrective -> corrective wins; both clean
        -> the original (index 0) wins the tie-break, so the bounded retry
        cannot churn the result for free."""
        garbled = Candidate(
            label="filename_derived",
            text="junk " * 100,
            char_count=500,
            garbled=True,
            engine="tesseract",
        )
        clean = Candidate(
            label="corrective_retry",
            text="مرحبا " * 100,
            char_count=600,
            garbled=False,
            engine="tesseract",
        )
        assert arbitrate([garbled, clean]) == 1, "corrective should win when it is not garbled"

        original = Candidate(
            label="filename_derived",
            text="hello " * 100,
            char_count=600,
            garbled=False,
            engine="tesseract",
        )
        corrective = Candidate(
            label="corrective_retry",
            text="world " * 100,
            char_count=600,
            garbled=False,
            engine="tesseract",
        )
        assert arbitrate([original, corrective]) == 0, "original should win when both are clean"


class TestD4DecisionPoints:
    """Task 6.3: decision points are registered with their choices."""

    def test_corrective_retry_and_dispatch_points_registered(self):
        from pageindex_mcp.obs.decision_points import point_for

        pt = point_for("d4_corrective_retry")
        assert pt is not None
        assert "corrective_retry" in pt.choices
        assert "skip_langs_match" in pt.choices
        assert "image_tesseract" in point_for("ocr_retry_dispatch_route").choices


class TestD4ArchitectureGuards:
    """Verify that the widened recovery methods still satisfy the bounded-retry
    architecture invariant: every caller of _execute_ocr_retry checks
    state.full_page_already_applied before the call, so the retry loop cannot
    run unbounded."""

    def test_all_ocr_retry_callers_have_full_page_guard(self):
        src = _src("client/recovery.py")
        tree = ast.parse(src)
        callers: set[str] = set()
        failures: list[str] = []
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef) or cls.name != "RecoveryMixin":
                continue
            for method in cls.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                retry_line = None
                for node in ast.walk(method):
                    if isinstance(node, ast.Attribute) and node.attr == "_execute_ocr_retry":
                        retry_line = node.lineno
                if retry_line is None:
                    continue
                callers.add(method.name)
                guard_line = None
                for node in ast.walk(method):
                    if isinstance(node, ast.If):
                        if_src = ast.get_source_segment(src, node)
                        if if_src and "full_page_already_applied" in if_src:
                            guard_line = node.lineno
                            break
                if guard_line is None or guard_line >= retry_line:
                    failures.append(
                        f"{method.name}: full_page_already_applied guard missing or "
                        f"after the _execute_ocr_retry call"
                    )
        assert not failures, "unbounded OCR retry paths:\n  " + "\n  ".join(failures)

        expected = {
            "_recover_garble_ocr",
            "_recover_low_content_ocr",
            "_recover_image_dominant_ocr",
        }
        assert expected <= callers, f"Missing expected callers: {expected - callers}"
