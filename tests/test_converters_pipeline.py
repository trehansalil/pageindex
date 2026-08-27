# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Converter pipeline integration and RFC pipeline tests."""

from __future__ import annotations

import base64
import copy
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp import converters
from pageindex_mcp.config import pipeline_config
from pageindex_mcp.converters import (
    _clip_text_contained,
    _document_level_text_fallback,
    _normalize_for_containment,
    _recover_picture_results,
    _recover_picture_text,
)
from pageindex_mcp.helpers import (
    _GATE_PRIORITY,
    _OVERSIZED_ORDINAL_RE,
    _flat_block_primary_text,
    _has_heading_markers,
    _ordinal_value,
    HARD_FAIL_DEFECTS,
    GateOutcome,
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    VerdictResult,
    VerdictThresholds,
    _segment_table_nodes,
    apply_promotions,
    classify_verdict,
    evaluate_gates,
    prepare_tree,
    split_oversized_leaf_nodes,
)
from pageindex_mcp.script import ScriptContext


# --- from test_pipeline.py ---

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _th() -> VerdictThresholds:
    return VerdictThresholds.from_config(pipeline_config)


def _well_formed() -> list:
    return [
        {
            "node_id": "1",
            "title": "Root",
            "text": "",
            "nodes": [
                {"node_id": "2", "title": "Ch1", "text": "a" * 100, "nodes": []},
                {"node_id": "3", "title": "Ch2", "text": "b" * 100, "nodes": []},
                {"node_id": "4", "title": "Ch3", "text": "c" * 100, "nodes": []},
            ],
        }
    ]


def _single_leaf(size: int = 1000) -> list:
    return [{"node_id": "1", "title": "Root", "text": "x" * size, "nodes": []}]


def _varied_text(i: int) -> str:
    paragraphs = [
        "The insurance contract shall be governed by the applicable laws and regulations.",
        "Premium payments are due on the first day of each calendar month without exception.",
        "Coverage extends to all listed beneficiaries as specified in the policy document.",
    ]
    return paragraphs[i % len(paragraphs)]


def _outcome_for(
    structure: list | None = None,
    defect: TreeDefect = TreeDefect.OK,
    all_defects: frozenset | None = None,
) -> GateOutcome:
    if structure is None:
        structure = _well_formed()
    th = _th()
    sig = TreeSignals.from_tree(structure, garble_threshold=th.garble_threshold)
    if all_defects is None:
        all_defects = frozenset()
    return GateOutcome(
        defect=defect,
        validate_reason=None,
        signals=sig,
        all_defects=all_defects,
        hard_fail_verdict=None,
    )


def _make_ok_gate_result(structure: list | None = None) -> TreeGateResult:
    if structure is None:
        structure = _well_formed()
    sig = TreeSignals.from_tree(structure, garble_threshold=_th().garble_threshold)
    return TreeGateResult(ok=True, defect=TreeDefect.OK, signals=sig, all_defects=frozenset())


def _make_gate_result(
    defect: TreeDefect,
    structure: list | None = None,
    all_defects: frozenset | None = None,
) -> TreeGateResult:
    if structure is None:
        structure = _well_formed()
    sig = TreeSignals.from_tree(structure, garble_threshold=_th().garble_threshold)
    if all_defects is None:
        all_defects = frozenset({defect}) if defect != TreeDefect.OK else frozenset()
    return TreeGateResult(
        ok=(defect == TreeDefect.OK),
        defect=defect,
        detail=defect.value,
        signals=sig,
        all_defects=all_defects,
    )


# =============================================================================
# apply_promotions
# =============================================================================


class TestApplyPromotions:
    def test_well_formed_passes(self):
        structure = [
            {
                "node_id": "1",
                "title": "Root",
                "text": "",
                "nodes": [
                    {
                        "node_id": str(i),
                        "title": f"Chapter {i}",
                        "text": _varied_text(i),
                        "nodes": [],
                    }
                    for i in range(2, 12)
                ],
            }
        ]
        outcome = _outcome_for(structure=structure)
        vr = apply_promotions(
            outcome,
            "",
            image_enrichment_ratio=None,
            inspector_class=None,
            th=_th(),
            expected_script=None,
        )
        assert vr.verdict == "PASS"

    def test_image_standalone_high_enrichment_passes(self):
        outcome = _outcome_for()
        vr = apply_promotions(
            outcome,
            "image_standalone",
            image_enrichment_ratio=0.95,
            inspector_class=None,
            th=_th(),
            expected_script=None,
        )
        assert vr.verdict == "PASS"

    def test_returns_verdict_result(self):
        outcome = _outcome_for()
        vr = apply_promotions(
            outcome,
            "",
            image_enrichment_ratio=None,
            inspector_class=None,
            th=_th(),
            expected_script=None,
        )
        assert isinstance(vr, VerdictResult)
        verdict, reason = vr
        assert isinstance(verdict, str)


# =============================================================================
# evaluate_gates
# =============================================================================


class TestEvaluateGates:
    def test_non_hard_fail_no_verdict(self):
        gr = _make_gate_result(TreeDefect.NODE_COUNT_LOW)
        outcome = evaluate_gates(_well_formed(), gr, None, _th())
        assert outcome.hard_fail_verdict is None

    def test_ok_defect_passes(self):
        gr = _make_gate_result(TreeDefect.OK)
        outcome = evaluate_gates(_well_formed(), gr, None, _th())
        assert outcome.hard_fail_verdict is None
        assert outcome.defect == TreeDefect.OK

    def test_cofiring_tiebreak(self):
        hf_list = sorted(HARD_FAIL_DEFECTS, key=lambda d: _GATE_PRIORITY.get(d, 999))
        if len(hf_list) < 2:
            pytest.skip("Need at least 2 hard-fail defects")
        worst, second = hf_list[0], hf_list[1]
        gr = _make_gate_result(TreeDefect.OK, all_defects=frozenset({worst, second}))
        outcome = evaluate_gates(_well_formed(), gr, None, _th())
        assert outcome.hard_fail_verdict is not None
        assert outcome.hard_fail_verdict.reason == worst.value


# =============================================================================
# Decomposition regression
# =============================================================================


def _decomposed_verdict(structure, content_class, validate_result=None, **kw):
    th = _th()
    expected_script = kw.pop("expected_script", None)
    flat = kw.pop("flat", False)
    source_selection = kw.pop("source_selection", False)
    image_enrichment_ratio = kw.pop("image_enrichment_ratio", None)
    if isinstance(expected_script, ScriptContext):
        bare_script = expected_script.dominant_script
    else:
        bare_script = expected_script
    outcome = evaluate_gates(structure, validate_result, expected_script, th, flat=flat)
    if outcome.hard_fail_verdict is not None:
        return outcome.hard_fail_verdict
    return apply_promotions(
        outcome,
        content_class,
        image_enrichment_ratio,
        None,
        th,
        bare_script,
        validate_result,
        source_selection=source_selection,
    )


# =============================================================================
# Verdict ledger
# =============================================================================


def _make_s3_error(code="NoSuchKey"):
    from minio.error import S3Error

    return S3Error(MagicMock(), code, "not found", "", "", "")


def _mock_minio():
    mc = MagicMock()
    store: dict[str, bytes] = {}

    def put_object(bucket, key, data, length, content_type=None):
        store[key] = data.read()

    def get_object(bucket, key):
        if key not in store:
            raise _make_s3_error("NoSuchKey")
        response = MagicMock()
        response.read.return_value = store[key]
        return response

    def list_objects(bucket, prefix="", recursive=False):
        return [type("O", (), {"object_name": k})() for k in store if k.startswith(prefix)]

    def remove_object(bucket, key):
        store.pop(key, None)

    mc.put_object.side_effect = put_object
    mc.get_object.side_effect = get_object
    mc.list_objects.side_effect = list_objects
    mc.remove_object.side_effect = remove_object
    mc._store = store
    return mc


class TestLedgerRemoval:
    """RFC-037 D3: persist_verdict_ledger and read_verdict_ledger removed."""

    def test_persist_verdict_ledger_not_importable(self):
        import pageindex_mcp.storage as storage_mod

        assert not hasattr(storage_mod, "persist_verdict_ledger")

    def test_read_verdict_ledger_not_importable(self):
        import pageindex_mcp.storage as storage_mod

        assert not hasattr(storage_mod, "read_verdict_ledger")


# =============================================================================
# Verdict authority
# =============================================================================


class TestVerdictAuthority:
    @pytest.mark.asyncio
    async def test_upsert_verdict_returns_winning_row(self):
        from pageindex_mcp.registry import upsert_verdict

        winning = {
            "doc_id": "abc",
            "verdict": "PASS",
            "pipeline_version": 4,
            "permanent_marginal": False,
            "verdict_computed_at": "2026-08-18T12:00:00Z",
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=winning)
        with patch("pageindex_mcp.registry.schema.get_pool", return_value=mock_pool):
            result = await upsert_verdict(
                "abc", {"verdict": "PASS", "verdict_computed_at": "2026-08-18T12:00:00Z"}
            )
        assert result["verdict"] == "PASS"


# =============================================================================
# _run_stages provenance
# =============================================================================


class TestRunStages:
    def test_return_type_and_order(self):
        from pageindex_mcp.converters import _run_stages

        stages = [("alpha", lambda m: m), ("beta", lambda m: m + "!")]
        md, records = _run_stages("x", stages)
        assert list(records.keys()) == ["alpha", "beta"]

    def test_failure_does_not_skip_next_stage(self):
        from pageindex_mcp.converters import _run_stages

        def fail(md):
            raise RuntimeError("boom")

        def ok(md):
            return md + " ok"

        md, records = _run_stages("start", [("fail", fail), ("ok", ok)])
        assert md == "start ok"
        assert records["fail"]["error"] is not None
        assert records["ok"]["error"] is None


# =============================================================================
# _text_layer_has_content
# =============================================================================


class TestTextLayerHasContent:
    def _make_page(self, text, region_text=None):
        page = MagicMock()

        def get_text_side_effect(mode="text", clip=None):
            if clip is not None and region_text is not None:
                return region_text
            return text

        page.get_text = MagicMock(side_effect=get_text_side_effect)
        return page

    def test_garbled_returns_false(self):
        from pageindex_mcp.converters import _text_layer_has_content
        from pageindex_mcp.helpers import GarbleReport

        garbled = GarbleReport(is_garbled=True, fired_prongs=frozenset({"test"}))
        with patch("pageindex_mcp.converters.pictures.detect_garble", return_value=garbled):
            assert _text_layer_has_content(self._make_page("A" * 100)) is False


# =============================================================================
# Picture-result skip signaling
# =============================================================================


class TestPictureResultSkip:
    def test_non_skipped_produces_figure(self):
        from pageindex_mcp.converters import PictureResult, splice_figure_markers

        md = "Text <!-- image --> more"
        pics = [
            PictureResult(ocr_text="Chart data", page=0, bbox={"l": 0, "t": 0, "r": 100, "b": 100})
        ]
        result = splice_figure_markers(md, pics)
        assert "[Figure: fig-0]" in result


# =============================================================================
# prepare_tree
# =============================================================================


class TestPrepareTree:
    def test_small_structure_unchanged(self):
        structure = [
            {
                "title": "S1",
                "text": "Short.",
                "level": 1,
                "nodes": [{"title": "Sub", "text": "Details.", "level": 2}],
            },
        ]
        assert prepare_tree(copy.deepcopy(structure)) == structure

    def test_split_then_segment_composition(self):
        sections = [f"Article ({i})\n\n" + "Body. " * 4000 for i in range(1, 5)]
        big_text = "\n\n".join(sections)
        structure = [{"title": "Doc", "text": big_text, "level": 1}]
        result = prepare_tree(copy.deepcopy(structure))
        manual = _segment_table_nodes(split_oversized_leaf_nodes(copy.deepcopy(structure)))
        assert result == manual


# =============================================================================
# Landscape / rotation renames
# =============================================================================


pytest.importorskip("fitz")
import fitz  # noqa: E402


class TestLandscapeRenames:
    def test_tag_landscape_pages(self, tmp_path):
        from pageindex_mcp.converters import _tag_landscape_pages_for_fallback

        doc = fitz.open()
        doc.new_page(width=600, height=800)
        path = str(tmp_path / "portrait.pdf")
        doc.save(path)
        doc.close()
        pages = _tag_landscape_pages_for_fallback(path)
        assert pages[0]["is_landscape"] is False


# =============================================================================
# _build_candidate equivalence
# =============================================================================


class TestBuildCandidate:
    def _old_mirrored(self, md):
        from pageindex_mcp.converters import (
            _inject_arabic_structural_headings,
            _inject_english_article_headings,
            _inject_german_clause_headings,
            _pre_inference_normalize,
        )

        md = _inject_arabic_structural_headings(md)
        md = _inject_german_clause_headings(md)
        md = _inject_english_article_headings(md)
        return _pre_inference_normalize(md)

    def test_empty(self):
        from pageindex_mcp.converters import _build_candidate

        assert _build_candidate("") == self._old_mirrored("")


# =============================================================================
# _has_structural_depth
# =============================================================================


# --- from test_rfc_pipeline.py ---

# ---------------------------------------------------------------------------
# Shared fixtures / helpers (from test_rfc_pipeline.py)
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
    "xray",
    "yankee",
    "zulu",
    "apple",
    "banana",
    "cherry",
    "date",
    "fig",
    "grape",
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


def _region(l, t, r, b, page=1):
    return {"page": page, "bbox": types.SimpleNamespace(l=l, t=t, r=r, b=b, coord_origin=None)}


def _make_fake_fitz(page_width: float, page_height: float, clip_text: str = ""):
    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        l=a[0],
        t=a[1],
        r=a[2],
        b=a[3],
        width=a[2] - a[0],
        height=a[3] - a[1],
    )

    class _FakePage:
        def __init__(self):
            self.rect = types.SimpleNamespace(height=page_height, width=page_width)
            self.rotation = 0

        def get_text(self, mode="text", *, clip=None):
            return clip_text

        def set_rotation(self, value):
            self.rotation = value

        def get_pixmap(self, *, clip=None, dpi=300):
            raise AssertionError("tesseract crop path must not run when clip_text is captured")

    page = _FakePage()

    class _FakeDoc:
        page_count = 1

        def __getitem__(self, idx):
            return page

        def close(self):
            pass

    fake.open = lambda path: _FakeDoc()
    return fake


def _tree_with_ratio(ratio: float, total_chars: int = 10000, n_other: int = 6) -> list:
    """Root node with one dominant leaf (`ratio` share of leaf chars) and
    `n_other` smaller leaves, so node_count and depth clear their gates
    (node_count=1+n_other+1 >= 3, depth=2) and only max_leaf_ratio varies."""
    max_leaf = round(ratio * total_chars)
    other_leaf = (total_chars - max_leaf) // n_other
    leaves = [{"title": "", "text": _text_of_length(max_leaf), "nodes": []}]
    leaves += [
        {"title": "", "text": _text_of_length(other_leaf), "nodes": []} for _ in range(n_other)
    ]
    return [{"title": "Root", "text": "", "nodes": leaves}]


# ---------------------------------------------------------------------------
# classify_verdict: PASS_MAX_LEAF_RATIO widened default (D0)
# ---------------------------------------------------------------------------


class TestClassifyVerdictPassMaxLeafRatio:
    def test_ratio_below_widened_default_passes(self, monkeypatch):
        """max_leaf_ratio=0.25 with default (unset) PASS_MAX_LEAF_RATIO=0.30 -> PASS.

        0.25 sits above the OLD default (0.20) but below the WIDENED default
        (0.30), so this is the exact regression case D0 fixes."""
        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        structure = _tree_with_ratio(0.25)
        assert classify_verdict(structure, "hierarchical", None) == ("PASS", "")

    def test_ratio_above_widened_default_stays_marginal(self, monkeypatch):
        """max_leaf_ratio=0.35 with default (unset) PASS_MAX_LEAF_RATIO=0.30 -> MARGINAL."""
        monkeypatch.delenv("PASS_MAX_LEAF_RATIO", raising=False)
        structure = _tree_with_ratio(0.35)
        verdict, reason = classify_verdict(structure, "hierarchical", None)
        assert verdict == "MARGINAL"
        assert reason == "leaf_concentration=0.35"


# ---------------------------------------------------------------------------
# _recover_picture_text: clip_text capture with containment guard (D1)
# ---------------------------------------------------------------------------


class TestRecoverPictureTextClipCapture:
    def test_clip_text_not_in_markdown_is_captured(self, monkeypatch):
        clip_text = "Revenue grew 42% year over year across all regions"
        md = "# Report\n\nSome unrelated heading content.\n\n<!-- image -->"
        fake_fitz = _make_fake_fitz(600.0, 800.0, clip_text)
        region = _region(0, 0, 100, 40)

        def _fail_if_called(path, langs):
            raise AssertionError("tesseract must not run for captured clip_text")

        monkeypatch.setattr(converters.pictures, "_tesseract_ocr_image", _fail_if_called)

        with patch.dict(sys.modules, {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", [region], ["eng"], md=md)

        assert 0 not in skip_reasons
        assert result[0]["ocr_text"] == clip_text

    def test_clip_text_already_in_markdown_skips_no_double_capture(self, monkeypatch):
        clip_text = "The quarterly revenue increased significantly this year"
        md = f"# Report\n\n{clip_text}\n\n<!-- image -->"
        fake_fitz = _make_fake_fitz(600.0, 800.0, clip_text)
        region = _region(0, 0, 100, 40)
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda path, langs: "should not matter"
        )

        with patch.dict(sys.modules, {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", [region], ["eng"], md=md)

        assert skip_reasons[0] == "clip_text_already_exported"
        assert 0 not in result

    def test_containment_helper_matches_reflowed_text_and_rejects_unrelated(self):
        """`_clip_text_contained` is whitespace/reflow-robust and returns
        False for genuinely unrelated content."""
        clip_text = "Revenue   grew\n42%  year-over-year"
        md_body = "revenue grew 42% year-over-year across all business units"
        md_norm = _normalize_for_containment(md_body)
        assert _clip_text_contained(clip_text, md_norm) is True

        unrelated_clip = "Completely unrelated chart label content here"
        unrelated_md = _normalize_for_containment(
            "This markdown body talks about something else entirely."
        )
        assert _clip_text_contained(unrelated_clip, unrelated_md) is False


# ---------------------------------------------------------------------------
# _document_level_text_fallback: image-dominant page fallback (D1)
# ---------------------------------------------------------------------------


class TestDocumentLevelTextLayerFallback:
    def _fake_pdfium_module(self, page_texts):
        fake = types.ModuleType("pypdfium2")

        class _FakeTextPage:
            def __init__(self, text):
                self._text = text

            def get_text_range(self):
                return self._text

        class _FakePage:
            def __init__(self, text):
                self._text = text

            def get_textpage(self):
                return _FakeTextPage(self._text)

        class _FakeDoc:
            def __init__(self, texts):
                self._pages = [_FakePage(t) for t in texts]

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                pass

        fake.PdfDocument = lambda path: _FakeDoc(page_texts)
        return fake

    def test_fallback_leaves_markdown_unchanged_on_garble_or_open_failure(self, monkeypatch):
        """RFC-024 D1 risk mitigation: a scanned page's thin mojibake text
        layer must never be appended (HR5), and a pdfium open failure must
        degrade gracefully -- both return the markdown unchanged."""
        from pageindex_mcp import helpers

        md = "<!-- image -->"
        garbled = "þÿ\x02\x01 ¤¤¤ \x03\x04 ÿþ" * 20
        fake_pdfium_garbled = self._fake_pdfium_module([garbled])
        from pageindex_mcp.helpers import GarbleReport
        _garbled = GarbleReport(is_garbled=True, fired_prongs=frozenset({"test"}))
        monkeypatch.setattr(helpers, "detect_garble", lambda text, **kw: _garbled)
        with patch.dict(sys.modules, {"pypdfium2": fake_pdfium_garbled}):
            result = _document_level_text_fallback(md, "/fake.pdf")
        assert result == md

        def _raise(path):
            raise RuntimeError("pdfium open failed")

        fake_pdfium_broken = types.ModuleType("pypdfium2")
        fake_pdfium_broken.PdfDocument = _raise
        with patch.dict(sys.modules, {"pypdfium2": fake_pdfium_broken}):
            result = _document_level_text_fallback(md, "/fake.pdf")
        assert result == md


# ---------------------------------------------------------------------------
# _recover_picture_text / _recover_picture_results: per-region crash
# isolation (D2)
# ---------------------------------------------------------------------------


def _make_fake_fitz_with_failures(page_width: float, page_height: float, failing_indices: set):
    """Fake ``fitz`` module whose page raises on ``get_pixmap`` for regions
    whose rect matches one of ``failing_indices`` (identified by ``l`` coord,
    used here as a stand-in region id)."""
    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        l=a[0],
        t=a[1],
        r=a[2],
        b=a[3],
        width=a[2] - a[0],
        height=a[3] - a[1],
    )

    class _FakePage:
        def __init__(self):
            self.rect = types.SimpleNamespace(height=page_height, width=page_width)
            self.rotation = 0

        def get_text(self, mode="text", *, clip=None):
            return ""

        def set_rotation(self, value):
            self.rotation = value

        def get_pixmap(self, *, clip=None, dpi=300):
            if clip.l in failing_indices:
                raise RuntimeError("simulated degenerate-region crop failure")
            return types.SimpleNamespace(tobytes=lambda fmt: b"PNG_FAKE")

    page = _FakePage()

    class _FakeDoc:
        page_count = 1

        def __getitem__(self, idx):
            return page

        def close(self):
            pass

    fake.open = lambda path: _FakeDoc()
    return fake, page


class TestRecoverPictureTextCrashIsolation:
    def test_all_regions_fail_returns_empty_gracefully(self, monkeypatch):
        fake_fitz, _page = _make_fake_fitz_with_failures(
            600.0, 800.0, failing_indices={100, 200, 300}
        )
        monkeypatch.setattr(converters.pictures, "_tesseract_ocr_image", lambda path, langs: "")
        regions = [
            _region(100, 0, 130, 30),
            _region(200, 0, 230, 30),
            _region(300, 0, 330, 30),
        ]

        with patch.dict(sys.modules, {"fitz": fake_fitz}):
            result, skip_reasons = _recover_picture_text("/fake.pdf", regions, ["eng"])

        assert result == {}
        assert set(skip_reasons.values()) == {"crop_error"}
        assert len(skip_reasons) == 3

    def test_recover_picture_results_returns_empty_on_total_failure(self, monkeypatch):
        """When ``_recover_picture_text`` itself raises (e.g. the PDF cannot be
        opened at all), the outer except in ``_recover_picture_results`` still
        returns an empty list rather than propagating."""
        md = "some heading\n\n<!-- image -->\n\nmore text"
        monkeypatch.setattr(converters.pictures, "_OCR_ESCALATION_PER_PICTURE", True)
        monkeypatch.setattr(
            converters.pictures,
            "_collect_picture_regions",
            lambda document: [_region(0, 0, 30, 30)],
        )

        def _boom(pdf_path, regions, langs, md=""):
            raise RuntimeError("pdf could not be opened")

        monkeypatch.setattr(converters.pictures, "_recover_picture_text", _boom)

        result = _recover_picture_results(md, document=object(), pdf_path="/fake.pdf")

        assert result == []


# ---------------------------------------------------------------------------
# tesseract_ocr_pdf_pages: dual rasterization backend (D4)
# ---------------------------------------------------------------------------

_PDFIUM_PNG = f"data:image/png;base64,{base64.b64encode(b'PDFIUM_PNG_FAKE').decode()}"
_FITZ_PNG = f"data:image/png;base64,{base64.b64encode(b'FITZ_PNG_FAKE').decode()}"


class TestTesseractOcrPdfPagesFitzFallback:
    async def test_pypdfium2_succeeds_fitz_not_called(self, monkeypatch):
        fitz_called = False

        def _fitz_fallback(pdf_path, dpi=200):
            nonlocal fitz_called
            fitz_called = True
            return [_FITZ_PNG]

        monkeypatch.setattr(
            converters.formats,
            "rasterize_pdf_pages",
            lambda pdf_path, dpi=200: [_PDFIUM_PNG],
        )
        monkeypatch.setattr(converters.formats, "rasterize_pdf_pages_fitz", _fitz_fallback)
        monkeypatch.setattr(
            converters.pictures, "_tesseract_ocr_image", lambda path, langs: "pdfium text"
        )

        result = await converters.tesseract_ocr_pdf_pages("/fake.pdf", ["eng"])

        assert result == "pdfium text"
        assert fitz_called is False

    async def test_fitz_fallback_disabled_preserves_pypdfium2_only_failure(self, monkeypatch):
        monkeypatch.setattr(converters.formats, "_D7_FITZ_FALLBACK_ENABLED", False)

        def _pdfium_boom(pdf_path, dpi=200):
            raise RuntimeError("CMap corruption: pypdfium2 render failed")

        fitz_called = False

        def _fitz_fallback(pdf_path, dpi=200):
            nonlocal fitz_called
            fitz_called = True
            return [_FITZ_PNG]

        monkeypatch.setattr(converters.formats, "rasterize_pdf_pages", _pdfium_boom)
        monkeypatch.setattr(converters.formats, "rasterize_pdf_pages_fitz", _fitz_fallback)

        with pytest.raises(RuntimeError, match="CMap corruption"):
            await converters.tesseract_ocr_pdf_pages("/fake.pdf", ["eng"])

        assert fitz_called is False


# ---------------------------------------------------------------------------
# split_oversized_leaf_nodes / _has_heading_markers / _ordinal_value:
# extended ordinal markers + blank-line fallback (D3)
# ---------------------------------------------------------------------------


class TestSplitOversizedLeafNodes:
    def test_clause_markers_detected_and_split_fires(self):
        """'Clause 1 ... Clause 2 ... Clause 3' is detected by
        `_has_heading_markers` (split-eligible even under max_chars) and an
        actual oversized leaf with those markers splits into 3 children."""
        assert _has_heading_markers("Clause 1 says X. Clause 2 says Y. Clause 3 says Z.") is True

        text = (
            f"Clause 1 {_text_of_length(3000)}\n"
            f"Clause 2 {_text_of_length(3000)}\n"
            f"Clause 3 {_text_of_length(3000)}"
        )
        tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
        split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
        assert len(tree[0]["nodes"]) == 3
        assert tree[0]["nodes"][0]["text"].startswith("Clause 1")

    def test_existing_ordinal_patterns_unchanged(self):
        """Article/Section/مادة patterns still match and Article splitting
        still succeeds -- no regression from the new marker types."""
        assert _OVERSIZED_ORDINAL_RE.search("Article 9") is not None
        assert _OVERSIZED_ORDINAL_RE.search("Section 4") is not None
        assert _OVERSIZED_ORDINAL_RE.search("المادة ٥") is not None

        text = (
            f"Article 1 {_text_of_length(3000)}\n"
            f"Article 2 {_text_of_length(3000)}\n"
            f"Article 3 {_text_of_length(3000)}"
        )
        tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
        split_oversized_leaf_nodes(tree, max_chars=50000, min_segments=3)
        assert len(tree[0]["nodes"]) == 3

    def test_ordinal_value_roman_numerals_and_letters(self):
        """'Part IV/V/VI' -> `_ordinal_value` returns correct int tuples via
        `_roman_to_int`; 'Annex A/B/C' -> correct int tuples via `ord()`."""
        m4 = _OVERSIZED_ORDINAL_RE.search("Part IV")
        m5 = _OVERSIZED_ORDINAL_RE.search("Part V")
        m6 = _OVERSIZED_ORDINAL_RE.search("Part VI")
        assert _ordinal_value(m4) == (4,)
        assert _ordinal_value(m5) == (5,)
        assert _ordinal_value(m6) == (6,)

        ma = _OVERSIZED_ORDINAL_RE.search("Annex A")
        mb = _OVERSIZED_ORDINAL_RE.search("Annex B")
        mc = _OVERSIZED_ORDINAL_RE.search("Annex C")
        assert _ordinal_value(ma) == (1,)
        assert _ordinal_value(mb) == (2,)
        assert _ordinal_value(mc) == (3,)

    def test_part_prose_false_positive_regression_guard(self):
        """'Part 2 of the agreement' repeated (non-sequential, same ordinal
        each time) is English prose making a cross-reference, not a heading
        sequence -> must NOT produce a spurious split."""
        text = (
            f"As mentioned in Part 2 of the agreement, {_text_of_length(2500)}\n\n"
            f"Part 2 of the agreement also states {_text_of_length(2500)}\n\n"
            f"Referring again to Part 2 of the agreement, {_text_of_length(2500)}"
        )
        tree = [{"node_id": "n1", "title": "root", "text": text, "nodes": []}]
        split_oversized_leaf_nodes(
            tree, max_chars=50000, min_segments=3, _tree_ratio=0.1, _tree_total=len(text) * 10
        )
        assert tree[0]["nodes"] == []


# ---------------------------------------------------------------------------
# _flat_block_primary_text / save_flat_doc: flat-doc char-count measurement (D6)
# ---------------------------------------------------------------------------


def _table_heavy_blocks() -> list:
    return [
        {
            "role": "table",
            "row_records": ["Tarif A | EUR 10", "Tarif B | EUR 20"],
        },
        {
            "role": "table",
            "row_records": ["Beitrag 1 | Stufe 1", "Beitrag 2 | Stufe 2"],
        },
    ]


def _text_only_blocks() -> list:
    return [
        {"role": "prose", "text": "Clause 1: introductory text."},
        {"role": "prose", "text": "Clause 2: further provisions."},
    ]


class TestFlatDocCharCount:
    def test_text_only_doc_char_count_unchanged_from_prior_behavior(self):
        blocks = _text_only_blocks()
        pre_fix_chars = sum(len(b.get("text", "")) for b in blocks)
        flat_char_count = sum(len(_flat_block_primary_text(b)) for b in blocks)
        assert flat_char_count == pre_fix_chars
        assert flat_char_count == sum(len(b["text"]) for b in blocks)
