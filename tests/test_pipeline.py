"""Zone-4 pipeline consolidated test suite (trimmed).

Core behavioral coverage for: apply_promotions, evaluate_gates, compute_verdict
decomposition regression, verdict ledger, prepare_tree, _run_stages,
_text_layer_has_content, picture-result normalization, landscape renames.
"""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.config import pipeline_config
from pageindex_mcp.helpers import (
    _GATE_PRIORITY,
    HARD_FAIL_DEFECTS,
    GateOutcome,
    TreeDefect,
    TreeGateResult,
    TreeSignals,
    VerdictResult,
    VerdictThresholds,
    _segment_table_nodes,
    apply_promotions,
    evaluate_gates,
    prepare_tree,
    split_oversized_leaf_nodes,
)
from pageindex_mcp.script import ScriptContext

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
            validate_result=_make_ok_gate_result(structure),
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
            validate_result=None,
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
            validate_result=None,
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


class TestLedger:
    def test_persist_and_read(self):
        from pageindex_mcp.storage import persist_verdict_ledger, read_verdict_ledger

        mc = _mock_minio()
        with patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=mc):
            persist_verdict_ledger("abc123", "PASS", "clean")
            assert read_verdict_ledger("abc123") == "PASS"

    def test_pass_not_downgraded(self):
        from pageindex_mcp.storage import persist_verdict_ledger, read_verdict_ledger

        mc = _mock_minio()
        with patch("pageindex_mcp.storage.minio_ops.get_minio", return_value=mc):
            persist_verdict_ledger("h1", "PASS", "clean")
            persist_verdict_ledger("h1", "FAIL", "garbling")
            assert read_verdict_ledger("h1") == "PASS"

    def test_graceful_on_minio_unavailable(self):
        from pageindex_mcp.storage import persist_verdict_ledger, read_verdict_ledger

        with patch("pageindex_mcp.storage.minio_ops.get_minio", side_effect=Exception("down")):
            persist_verdict_ledger("h1", "PASS", "clean")
            assert read_verdict_ledger("h1") is None


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
