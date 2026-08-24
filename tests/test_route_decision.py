"""Route decision + config + verdict tiebreak tests (trimmed)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from pageindex_mcp.converters import reconstruct_bidi_order
from pageindex_mcp.helpers import (
    _GATE_PRIORITY,
    GATE_TABLE,
    Route,
    TreeDefect,
    TreeGateResult,
    _flat_block_primary_text,
    compute_verdict,
    decide_route,
)
from pageindex_mcp.script import decide_rtl

CLIENT_PATH = Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp" / "client.py"


@pytest.fixture(autouse=True)
def _restore_pipeline_config():
    yield
    from pageindex_mcp.config import reset_pipeline_config

    reset_pipeline_config()


def _varied(seed: int, n: int = 60) -> str:
    return " ".join(f"word{seed}n{j}alpha" for j in range(n))


def _leaf(title: str, text: str, **extra) -> dict:
    return {"title": title, "text": text, "nodes": [], **extra}


def _single_leaf(size: int = 1000) -> list:
    return [{"node_id": "1", "title": "Root", "text": "x " * size, "nodes": []}]


class TestPipelineConfig:
    def test_frozen_dataclass(self):
        from pageindex_mcp.config import PipelineConfig

        cfg = PipelineConfig.from_env()
        assert dataclasses.is_dataclass(cfg)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.pass_max_leaf_ratio = 0.99  # type: ignore[misc]

    def test_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("PASS_MAX_LEAF_RATIO", "0.25")
        from pageindex_mcp.config import PipelineConfig

        cfg = PipelineConfig.from_env()
        assert cfg.pass_max_leaf_ratio == 0.25


class TestPrimaryText:
    def test_prose_block_returns_text(self):
        assert _flat_block_primary_text({"text": "content", "role": "prose"}) == "content"

    def test_image_block_returns_empty(self):
        block = {"role": "image", "ocr_text": "OCR", "description": "pic"}
        assert _flat_block_primary_text(block) == ""


class TestDecideRoute:
    def test_all_defects_return_valid_route(self):
        for defect in TreeDefect:
            for flag in (True, False):
                assert isinstance(decide_route(defect, flat_routing_enabled=flag), Route)

    @pytest.mark.parametrize("defect", [TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW])
    def test_flat_enabled_yields_flat(self, defect):
        assert decide_route(defect, flat_routing_enabled=True) == Route.FLAT

    @pytest.mark.parametrize("defect", [TreeDefect.NODE_COUNT_LOW, TreeDefect.DEPTH_LOW])
    def test_flat_disabled_yields_reject(self, defect):
        assert decide_route(defect, flat_routing_enabled=False) == Route.REJECT

    def test_ok_routes_to_tree(self):
        assert decide_route(TreeDefect.OK) == Route.TREE

    def test_garbling_routes_to_tree(self):
        assert decide_route(TreeDefect.GARBLING) == Route.TREE

    def test_empty_node_contamination_persist_fail(self):
        assert decide_route(TreeDefect.EMPTY_NODE_CONTAMINATION) == Route.PERSIST_FAIL


class TestDecideRtl:
    def test_reversed_arabic_detected(self):
        text = "\n".join(["ةدام ةدام ةدام lines"] * 4)
        assert decide_rtl(text).reversed is True

    def test_correct_arabic_not_reversed(self):
        text = "\n".join(["في هذا النص العربي الطويل نجد أن القوانين"] * 3)
        assert decide_rtl(text).reversed is False

    def test_empty_not_reversed(self):
        assert decide_rtl("").reversed is False


class TestReconstructBidiOrder:
    def test_empty(self):
        text, _ = reconstruct_bidi_order("")
        assert text == ""

    def test_english_unchanged(self):
        eng = "This plain English text paragraph no Arabic."
        text, _ = reconstruct_bidi_order(eng)
        assert text == eng


class TestHardFailTiebreak:
    def test_garbling_severity_lower_than_low_content_density(self):
        assert _GATE_PRIORITY[TreeDefect.GARBLING] < _GATE_PRIORITY[TreeDefect.LOW_CONTENT_DENSITY]

    def test_masked_cofire_picks_most_severe(self):
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.BIDI_DEGRADED,
            all_defects=frozenset(
                {TreeDefect.BIDI_DEGRADED, TreeDefect.GARBLING, TreeDefect.LOW_CONTENT_DENSITY}
            ),
        )
        result = compute_verdict(_single_leaf(), "flat_prose", gate)
        assert result.verdict == "FAIL"
        assert result.reason == TreeDefect.GARBLING.value

    def test_severity_order_matches_enumerate(self):
        enumerate_based = {defect: idx for idx, (_fn, defect) in enumerate(GATE_TABLE)}
        assert enumerate_based == _GATE_PRIORITY
