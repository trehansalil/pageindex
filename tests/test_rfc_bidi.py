"""Consolidated tests for RFC-027 (bidi/RTL + garble/quality-gate hardening).

Merges test_rfc027_d0.py .. test_rfc027_d7.py into one file, grouped by the
production function/class each group exercises:

- D0: `_flat_block_primary_text` vs `_flat_block_text` (enrichment exclusion)
- D1: garble detection in `image_enrichment_promoted` + post-splice recheck
- D2: low-content OCR escalation gate (client.py inline condition)
- D3: RTL-reversal detection + repair-first flow (`validate_tree`,
      `reconstruct_bidi_order`, `decide_rtl`)
- D4: Arabic structural heading injection (`_inject_arabic_structural_headings`)
- D5: small-doc leaf-ratio dispensation (`classify_verdict`)
- D6: duplicate `<!-- image -->` marker dedup
- D7: page-count guard + chunked-Docling route for oversized PDFs
"""

import os
import re
import tempfile

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.conftest import filler_text

import fitz

from pageindex_mcp import client as client_mod
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.client import images as _img
from pageindex_mcp.client import indexer as _idx
from pageindex_mcp.client import recovery as _rec
from pageindex_mcp.converters import (
    _inject_arabic_structural_headings,
    _max_heading_level,
    _recover_heading_depth,
    reconstruct_bidi_order,
    splice_figure_markers,
)
from pageindex_mcp.helpers import (
    _flat_block_primary_text,
    classify_verdict,
    validate_tree,
)


# ---------------------------------------------------------------------------
# D0: _flat_block_primary_text excludes enrichment, _flat_block_text keeps it
# ---------------------------------------------------------------------------
class TestFlatBlockPrimaryTextExcludesEnrichment:
    def test_image_block_with_only_enrichment_returns_empty(self):
        """An image block with no 'text' key but ocr_text/description must
        contribute 0 chars to `_flat_block_primary_text` -- enrichment
        metadata is not document content."""
        block = {"role": "image", "ocr_text": "chart says 42%", "description": "a bar chart"}
        assert _flat_block_primary_text(block) == ""

    def test_table_block_falls_back_to_row_records(self):
        """Table blocks carry no 'text' key by design; row_records are
        document content, not enrichment, so they ARE included."""
        block = {"role": "table", "row_records": ["row one", "row two"]}
        assert _flat_block_primary_text(block) == "row one\nrow two"


# ---------------------------------------------------------------------------
# D1: image_enrichment_promoted garble gate + post-splice D3B recheck
# ---------------------------------------------------------------------------
def _digit_blob(total=3277, digit_frac=0.705):
    """Mirrors ward-597: ~3,277 chars, ~70.5% digit ratio -- clears the
    500-char floor but is numeric-junk garbage, not real content."""
    n_digits = round(total * digit_frac)
    n_filler = total - n_digits
    filler = ("barcode " * ((n_filler // 8) + 1))[:n_filler]
    return "9" * n_digits + filler


def _structure_with_text(text):
    return [{"node_id": "1", "title": "", "text": text, "nodes": []}]


class TestImageEnrichmentPromotedGarbleGate:
    def test_digit_noise_above_floor_is_not_promoted_to_pass(self):
        """A 70%-digit blob above the char floor must not return PASS -- the
        garble check falls through to the ordinary max_leaf_ratio gate
        instead of promoting."""
        blob = _digit_blob()
        assert len(blob) >= 500
        structure = _structure_with_text(blob)
        verdict, _reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.85
        )
        assert verdict != "PASS"

    def test_legitimate_blob_above_floor_still_passes(self):
        """A legitimate low-digit-ratio blob above the floor is not a
        false-positive -- PASS is still reachable."""
        structure = [
            {"node_id": str(i), "title": "", "text": "x" * 200, "nodes": []}
            for i in range(3)
        ]
        verdict, reason = classify_verdict(
            structure, "flat_mixed", None, image_enrichment_ratio=0.85
        )
        assert verdict == "PASS"
        assert reason == "image_enrichment_promoted"


# ---------------------------------------------------------------------------
# D2: low-content OCR escalation for .pdf documents rejected as node_count<3
# ---------------------------------------------------------------------------
def _escalation_fires(ok: bool, reason: str, total_chars: int, ext: str = ".pdf") -> bool:
    """Reproduces client.py:~987-991 -- the OCR-escalation trigger,
    including the RFC-027 D2 low-content branch."""
    low_content_ocr_eligible = (
        reason == "node_count<3" and total_chars < client_mod.LOW_CONTENT_OCR_CHAR_FLOOR
    )
    return (
        not ok
        and (reason in ("garbling", "node_garbling") or low_content_ocr_eligible)
        and ext == ".pdf"
        and _rec._OCR_ESCALATION_GARBLE
    )


class TestLowContentOcrEscalationBoundaries:
    def test_zero_chars_zero_nodes_fires(self):
        """A fully empty structure (MOU MOHRE-style) escalates."""
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=0) is True

    def test_char_floor_boundary_299_fires_300_does_not(self):
        """299 chars is just under the 300-char floor (escalates); 300 is at
        the floor -- exclusive-below, so it does NOT escalate."""
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=299) is True
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=300) is False


# ---------------------------------------------------------------------------
# D3: RTL-reversal detection (validate_tree) + repair-first flow
# ---------------------------------------------------------------------------
# Arabic text with no `_AR_COMMON_WORDS` hits and no `ال`-prefixed definite
# articles in EITHER direction (country names) -- both the forward and
# get_display()-reordered readability score come out to 0.
_ZERO_SCORE_TEXT = "قطر مصر سوريا لبنان تونس كندا اسبانيا"

# Genuinely visual/glyph-order Arabic (RFC-015 D7's known "visual" fixture) --
# the forward reading scores 0 while get_display() recovers common-word
# matches, so this line reads backwards.
_VISUAL_LINE = "رارق سلجم ءارزولا مقر ةنسل نأشب ميظنت تاقالع لمعلا يف رطق"
_VISUAL_LINE_2 = "رارقلا كلذ لدعملا ةدراولا صوصنلا قفو لمعلا ماكحأ ذيفنت"

# Genuinely logical-order Arabic for the non-regression / "already correct"
# side of each check.
_LOGICAL_LINE = "قرار مجلس الوزراء رقم لسنة بشأن تنظيم علاقات العمل وتعديلاته"


def _reversed_tree() -> list:
    return [
        {
            "title": "الباب الأول",
            "text": "",
            "start_index": 0,
            "nodes": [
                {"title": "المادة الأولى", "text": _VISUAL_LINE, "start_index": 1, "nodes": []},
                {"title": "المادة الثانية", "text": _VISUAL_LINE_2, "start_index": 2, "nodes": []},
            ],
        }
    ]


def _logical_tree() -> list:
    return [
        {
            "title": "الباب الأول",
            "text": "",
            "start_index": 0,
            "nodes": [
                {"title": "المادة الأولى", "text": _LOGICAL_LINE, "start_index": 1, "nodes": []},
                {
                    "title": "المادة الثانية",
                    "text": _LOGICAL_LINE + " هذا القانون",
                    "start_index": 2,
                    "nodes": [],
                },
            ],
        }
    ]


def _repair_first(structure: list, expected_script: str | None = None) -> tuple[bool, str]:
    """Mirrors client.py's RFC-027 D3 repair-first block (~line 1053-1076):
    on `rtl_reversal`, attempt `reconstruct_bidi_order` on every node's
    title/text and re-validate BEFORE deciding the verdict."""
    ok, reason = validate_tree(structure, expected_script=expected_script)
    if not ok and reason == "rtl_reversal":

        def _repair(nodes: list) -> None:
            for n in nodes:
                for key in ("title", "text"):
                    val = n.get(key)
                    if isinstance(val, str) and val:
                        n[key], _ = reconstruct_bidi_order(val)
                _repair(n.get("nodes") or [])

        _repair(structure)
        ok, reason = validate_tree(structure, expected_script=expected_script)
    return ok, reason


class TestValidateTreeRtlReversal:
    def test_reversed_arabic_tree_flagged(self):
        ok, reason = validate_tree(_reversed_tree())
        assert (ok, reason) == (False, "rtl_reversal")

    def test_logical_arabic_tree_not_flagged(self):
        ok, reason = validate_tree(_logical_tree())
        assert (ok, reason) != (False, "rtl_reversal")


class TestRepairFirstFlow:
    """RFC-027 D3: `rtl_reversal` must never hard-FAIL before
    `reconstruct_bidi_order` has been attempted."""

    def test_repair_converges_tree_accepted(self):
        ok, reason = _repair_first(_reversed_tree())
        assert (ok, reason) == (True, "")

    def test_repair_does_not_converge_falls_to_fail_path(self):
        # A no-op repair (mirrors reconstruct_bidi_order failing to converge)
        # must leave the verdict at rtl_reversal, not silently accept it.
        structure = _reversed_tree()

        def _noop_repair(nodes: list) -> None:
            for n in nodes:
                _noop_repair(n.get("nodes") or [])

        _noop_repair(structure)
        ok, reason = validate_tree(structure)
        assert (ok, reason) == (False, "rtl_reversal")


# ---------------------------------------------------------------------------
# D4: Arabic structural heading injection -> depth-recovery integration
# ---------------------------------------------------------------------------
# Mirrors marsoom-biqanoon's structure: a top-level بمرسوم title, two الباب
# parts each containing مادة articles, plus a long trailing paragraph whose
# FIRST WORDS quote "المادة 2"/"الباب"/"الفصل" mid-sentence -- the injection
# gate must not promote it.
_SYNTHETIC_DOC = """# مرسوم بقانون

قرار مجلس الوزراء بشأن تنظيم علاقات العمل.

الباب الأول
أحكام عامة

مادة 1
يسري هذا القانون على جميع العاملين.

مادة 2
تعريفات هذا القانون كما يلي.

الباب الثاني
شروط العمل

مادة 3
يجب على صاحب العمل الالتزام بالشروط.

هذا النص يشير إلى ما ورد في المادة 2 من هذا القانون بشأن التعريفات وتوضيحها في السياق العام للفصل الأول من هذا الباب الذي يحدد أحكاما عامة تفصيلية طويلة.
"""


class TestInjectArabicStructuralHeadingsBlockStart:
    @pytest.fixture(autouse=True)
    def _disable_density_guard(self, monkeypatch):
        import pageindex_mcp.converters.headings as _h
        monkeypatch.setattr(_h, "_AR_HEADING_MIN_CONTENT_CHARS", 0)

    def test_bab_at_block_start_promoted_to_h1(self):
        md = "مقدمة النص.\n\nالباب الأول\nأحكام عامة\n"
        result = _inject_arabic_structural_headings(md)
        assert "\n# الباب الأول\n" in result

    def test_maddah_at_block_start_promoted_to_h2(self):
        md = "مقدمة النص.\n\nمادة 1\nنص المادة الأولى.\n"
        result = _inject_arabic_structural_headings(md)
        assert "\n## مادة 1\n" in result


class TestDepthRecoveryOnInjectedHeadings:
    """RFC-027 D4 -> D3-chain integration: injected headings must feed the
    EXISTING `_recover_heading_depth` chain (`_relevel_by_containment` ->
    `_relevel_by_numbering` -> outline) and produce a tree with depth >= 2,
    matching an Arabic legal doc's English twin structure."""

    @pytest.fixture(autouse=True)
    def _disable_density_guard(self, monkeypatch):
        import pageindex_mcp.converters.headings as _h
        monkeypatch.setattr(_h, "_AR_HEADING_MIN_CONTENT_CHARS", 0)

    def test_synthetic_marsoom_biqanoon_reaches_depth_two(self):
        injected = _inject_arabic_structural_headings(_SYNTHETIC_DOC)
        recovered = _recover_heading_depth(injected, {}, "")
        assert _max_heading_level(recovered) >= 2

    def test_without_injection_stays_flat(self):
        # Non-regression control: skipping injection leaves al-bab/al-maddah
        # as plain prose, so the depth-recovery chain has nothing to nest --
        # confirms the injection step is load-bearing, not incidental.
        recovered = _recover_heading_depth(_SYNTHETIC_DOC, {}, "")
        assert _max_heading_level(recovered) < 2


# ---------------------------------------------------------------------------
# D5: small_doc_promoted leaf-ratio dispensation for very small trees
# ---------------------------------------------------------------------------
def _flat_leaf_tree(chars_per_leaf: list[int]) -> list:
    """A flat sibling tree (depth == 1) with one leaf per entry in
    ``chars_per_leaf``, using prose-shaped filler so improved garble
    detection does not flag test fixtures."""
    return [
        {"node_id": str(i), "title": "", "text": filler_text(n, i), "nodes": []}
        for i, n in enumerate(chars_per_leaf)
    ]


class TestSmallDocLeafRatioDispensation:
    def test_node_count_5_leaf_ratio_39_promotes_to_pass(self):
        """node_count == 5, leaf_concentration == 0.39: exceeds the base
        PASS_MAX_LEAF_RATIO (0.30) and the pre-D5 small-doc bound (0.20),
        but is under the relaxed 0.40 bound for node_count <= 5 -- must
        promote via small_doc_promoted (GHV-TKV-Tarif.pdf case)."""
        structure = _flat_leaf_tree([39, 16, 15, 15, 15])
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert (verdict, reason) == ("PASS", "small_doc_promoted")

    def test_node_count_8_leaf_ratio_35_stays_margin(self):
        """node_count == 8 (in the 6-10 band): the relaxed 0.40 bound does
        NOT apply, so leaf_concentration == 0.35 (> the retained 0.20
        bound) must NOT promote -- verdict stays MARGINAL, not PASS."""
        structure = _flat_leaf_tree([35, 10, 10, 10, 10, 10, 10, 5])
        verdict, _reason = classify_verdict(structure, "flat_prose", None)
        assert verdict != "PASS"


# ---------------------------------------------------------------------------
# D6: deduplicate identical adjacent <!-- image --> markers
# ---------------------------------------------------------------------------
_DEDUP_RE = re.compile(r"(<!-- image -->)\s*(?=<!-- image -->)")


def _fake_settings():
    return SimpleNamespace(
        openai_api_key="k",
        openai_base_url="https://api.openai.com/v1",
        azure_api_version=None,
        llm_model="gpt-test",
        minio_secure=False,
        minio_endpoint="localhost:9000",
        minio_bucket="pageindex",
        flat_doc_routing=True,
        vlm_fallback=False,
        vlm_model="gpt-4.1",
        vlm_describe_images=False,
        pii_corpus=False,
    )


async def _run_index_with_markdown(monkeypatch, markdown: str, source_bytes: bytes):
    """Drive CustomPageIndexClient.index() over a fake .jpg, capturing the
    pic_results list passed to splice_figure_markers."""
    fd, jpg_path = tempfile.mkstemp(suffix=".jpg")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(source_bytes)

        monkeypatch.setattr(_idx, "settings", _fake_settings())
        monkeypatch.setattr(_img, "settings", _fake_settings())
        monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
        monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
        monkeypatch.setattr(_idx, "hash_cache_set", lambda *a, **kw: None)
        monkeypatch.setattr(_idx, "validate_tree", lambda s, **kw: (False, "depth<2"))
        monkeypatch.setattr(
            _img,
            "route_and_extract_flat",
            lambda md: ("flat_prose", [{"role": "prose", "text": "x"}]),
        )
        monkeypatch.setattr(_idx, "save_flat_doc", lambda *a, **kw: None)
        monkeypatch.setattr(_idx, "save_doc", lambda *a, **kw: None)
        monkeypatch.setattr(_idx, "save_raw", lambda *a, **kw: None)
        monkeypatch.setattr(_idx, "save_doc_meta", lambda *a, **kw: None)
        monkeypatch.setattr(_idx, "FLAT_DOCS_TOTAL", MagicMock())
        monkeypatch.setattr(_idx, "LOW_QUALITY_TREES", MagicMock())
        monkeypatch.setattr(_idx, "ensure_tessdata", lambda langs: langs)
        monkeypatch.setattr(_idx, "image_to_markdown", lambda path, langs: markdown)

        captured_pics = []
        orig_splice = splice_figure_markers

        def spy_splice(md, pics):
            captured_pics.extend(pics)
            return orig_splice(md, pics)

        monkeypatch.setattr(_img, "splice_figure_markers", spy_splice)

        c = CustomPageIndexClient(api_key="test-key")

        async def _fake_tree(md_path):
            return {
                "structure": [{"node_id": "n1", "text": "x", "nodes": []}],
                "doc_description": "",
            }

        monkeypatch.setattr(c, "_run_md_to_tree", _fake_tree)

        await c.index(jpg_path)
        return captured_pics
    finally:
        if os.path.exists(jpg_path):
            os.unlink(jpg_path)


class TestMarkerDedupRegex:
    """Unit-level: the dedup regex itself, mirroring the exact pattern used
    at client.py's standalone-image branch."""

    def test_whitespace_separated_markers_collapse(self):
        md = "<!-- image -->\n\n<!-- image -->"
        assert _DEDUP_RE.sub("", md).count("<!-- image -->") == 1

    def test_directly_adjacent_markers_collapse(self):
        md = "<!-- image --><!-- image -->"
        assert _DEDUP_RE.sub("", md).count("<!-- image -->") == 1


# ---------------------------------------------------------------------------
# D7: page-count guard + chunked-Docling route for oversized PDFs, with a
# pymupdf text-layer-only fallback on chunk timeout.
# ---------------------------------------------------------------------------
class _FakePage:
    def __init__(self, text: str):
        self._text = text

    def get_text(self, *_args, **_kwargs) -> str:
        return self._text


class _FakeDoc:
    """Stand-in for a read-mode ``fitz.Document``."""

    def __init__(self, page_count: int, text: str):
        self.page_count = page_count
        self._pages = [_FakePage(text) for _ in range(page_count)]
        self.closed = False

    def __len__(self) -> int:
        return self.page_count

    def __iter__(self):
        return iter(self._pages)

    def __getitem__(self, index: int) -> _FakePage:
        return self._pages[index]

    def load_page(self, index: int) -> _FakePage:
        return self._pages[index]

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()
        return False


class _FakeWriterDoc:
    """Stand-in for the empty ``fitz.open()`` document each chunk is built in."""

    def __init__(self, recorder: "_FakeFitz"):
        self._recorder = recorder
        self._page_count = 0
        self.closed = False

    def insert_pdf(self, src, from_page=None, to_page=None):
        self._recorder.inserts.append((from_page, to_page))
        self._recorder.insert_sources.append(src)
        # pymupdf's ``to_page`` is INCLUSIVE -- mirror that here so a chunk cut
        # from the half-open slice [start, end) materializes exactly
        # ``end - start`` pages. An off-by-one in the port shows up as a wrong
        # page count in the timeout-fallback text below.
        self._page_count = to_page - from_page + 1

    def save(self, path, *_args, **_kwargs):
        self._recorder.saves.append(path)
        self._recorder.chunk_page_counts[path] = self._page_count

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()
        return False


class _FakeFitz:
    """Records every ``fitz.open`` call ``converters.py`` makes.

    ``open(path)`` yields a read doc; ``open()`` (no args) yields the writer doc
    used for chunk assembly. A path previously written by ``save()`` re-opens
    with the page count that chunk actually received, so the timeout fallback
    reads back exactly the pages the split produced.
    """

    def __init__(self, page_count: int, text: str = "lorem ipsum"):
        self.source_page_count = page_count
        self.text = text
        self.opened_paths: list[str] = []
        self.inserts: list[tuple[int, int]] = []
        self.insert_sources: list[object] = []
        self.saves: list[str] = []
        self.chunk_page_counts: dict[str, int] = {}
        self.docs: list[_FakeDoc] = []

    def open(self, path=None, *_args, **_kwargs):
        if path is None:
            return _FakeWriterDoc(self)
        self.opened_paths.append(path)
        doc = _FakeDoc(self.chunk_page_counts.get(path, self.source_page_count), self.text)
        self.docs.append(doc)
        return doc


def _patch_fitz(monkeypatch, page_count: int, text: str = "lorem ipsum") -> _FakeFitz:
    """Patch ``fitz.open`` where ``converters.py`` looks it up: it does a
    function-local ``import fitz``, so the module attribute is the seam."""
    recorder = _FakeFitz(page_count, text)
    monkeypatch.setattr(fitz, "open", recorder.open)
    return recorder
