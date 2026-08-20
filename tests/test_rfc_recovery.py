"""Consolidated RFC-028 recovery tests (D0-D5, D7).

Merged from test_rfc028_d0.py .. test_rfc028_d7.py. Each test class is
grouped by the production function/behavior it exercises:

  D0 - dynamic converter-subprocess timeout wiring (converters/worker)
  D1 - Arabic structural heading injection (converters)
  D2 - Arabic Presentation-Forms garble detection (helpers)
  D3 - RTL-reversal vocabulary + morphology detection (converters/helpers)
  D4 - OCR retry keep-best-content logic (client.py behavior, reproduced)
  D5 - picture-OCR language derivation + dedup splicing (converters)
  D7 - Roman-numeral oversized-leaf ordinal splitting (helpers)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pageindex_mcp.converters as converters
from pageindex_mcp.converters import (
    _AR_COMMON_WORDS,
    _CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S,
    _IMAGE_MARKER,
    _arabic_readability_score,
    _inject_arabic_structural_headings,
    _max_heading_level,
    _recover_heading_depth,
    _recover_picture_results,
    chunked_docling_timeout_s,
    decide_rtl,
    detect_ocr_langs,
    probe_conversion_route,
    splice_picture_text_for_tree,
)
from pageindex_mcp.helpers import (
    _OVERSIZED_ORDINAL_RE,
    BULK_PROFILE,
    _flatten_tree_text,
    _infer_script,
    _ordinal_value,
    _word_has_reversed_morphology,
    check_garble,
    split_oversized_leaf_nodes,
)
from pageindex_mcp.worker import (
    CHILD_GRACE_SECONDS,
    CHILD_TIMEOUT,
    JOB_TIMEOUT,
    _run_converter_subprocess,
)


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
                "pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)
            ),
            patch("pageindex_mcp.worker.asyncio.timeout", _RecordingTimeout(sink)),
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
                "pageindex_mcp.worker.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)
            ),
            patch("pageindex_mcp.worker.asyncio.timeout", _RecordingTimeout(sink)),
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
_CLEAN_TEXT = "قرار مجلس الوزراء بشأن تنظيم علاقات العمل والتعديلات المرتبطة به"


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
                _flatten_tree_text(pre_retry_structure), expected_script=expected_script
            , profile=BULK_PROFILE)
            and not check_garble(
                _flatten_tree_text(post_retry_structure), expected_script=expected_script
            , profile=BULK_PROFILE)
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

_WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
    "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
).split()


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
