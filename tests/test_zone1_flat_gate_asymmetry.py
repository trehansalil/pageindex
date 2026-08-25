"""Zone-1 Flat Gate Asymmetry: per-block garble detection tests.

Verifies the Zone-1 remediation that replaces the whole-blob flat-path
garble check with per-block detection via _garble_check_flat_blocks,
eliminating dilution where a single garbled table amid clean prose
passes the whole-blob threshold.
"""

from __future__ import annotations

import pytest

from pageindex_mcp.helpers import (
    ScriptContext,
    _garble_check_flat_blocks,
    _flat_block_primary_text,
)
from pageindex_mcp.helpers.garble import GarbleConfig, GarbleReport, detect_garble
from pageindex_mcp.helpers.gates import FLAT_GATE_COVERAGE
from pageindex_mcp.helpers.types import Route, TreeDefect, decide_route

import ast
import inspect
import textwrap


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


# ── Garbled TABLE block amid clean prose ──────────────────────────

class TestPerBlockGarbleCatchesGarbledTable:
    """Contract: per-block check catches a garbled TABLE block even when
    the surrounding prose is clean."""

    def test_garbled_table_among_clean_prose(self):
        garbled_digits = "1234567890" * 60
        blocks = [
            {"role": "prose", "text": "The quick brown fox jumps over the lazy dog near the river bank. Birds sing loudly in tall oak trees during warm summer mornings. Fresh coffee aroma fills the kitchen. "},
            {"role": "table", "text": garbled_digits, "row_records": []},
            {"role": "prose", "text": "Cars drive along the highway while pedestrians cross at marked intersections safely. Mountains rise above the valley floor creating beautiful landscape views. "},
        ]
        report = _garble_check_flat_blocks(
            blocks,
            script_context=_default_ctx(),
            config=_default_config(),
        )
        assert report is not None
        assert bool(report) is True
        assert isinstance(report, GarbleReport)

    def test_all_clean_blocks_pass(self):
        blocks = [
            {"role": "prose", "text": "The quick brown fox jumps over the lazy dog near the river bank. Birds sing loudly in tall oak trees during warm summer mornings. Fresh coffee aroma fills the kitchen. "},
            {"role": "prose", "text": "Cars drive along the highway while pedestrians cross at marked intersections safely. Mountains rise above the valley floor creating beautiful views. "},
        ]
        report = _garble_check_flat_blocks(
            blocks,
            script_context=_default_ctx(dominant_script="Latn"),
            config=_default_config(),
        )
        assert report is None


# ── Dilution immunity ─────────────────────────────────────────────

class TestDilutionImmunity:
    """Regression (RFC-027 #5330 / RFC-026): whole-blob digit-ratio passes
    but one block individually exceeds 0.60 — per-block check catches it."""

    def test_single_garbled_block_not_diluted(self):
        clean = "The quick brown fox jumps over the lazy dog near the river bank. Birds sing loudly in tall oak trees during warm summer mornings. Fresh coffee aroma fills the kitchen as sunlight streams through windows. "
        garbled = "9" * 600
        blocks = [
            {"role": "prose", "text": clean},
            {"role": "prose", "text": clean},
            {"role": "prose", "text": clean},
            {"role": "prose", "text": clean},
            {"role": "table", "text": garbled},
        ]
        report = _garble_check_flat_blocks(
            blocks,
            script_context=_default_ctx(),
            config=_default_config(),
        )
        assert report is not None
        assert report.garble_ratio == pytest.approx(1 / 5, abs=0.01)


# ── had_presentation_forms threading ──────────────────────────────

class TestPresentationFormsThreading:
    """Regression (RFC-019 D2 / RFC-028 D2): had_presentation_forms must
    thread through to the per-block detect_garble calls."""

    def test_presentation_forms_flag_reaches_detect_garble(self):
        from unittest.mock import patch

        calls = []
        original_detect = detect_garble

        def spy_detect(text, **kwargs):
            calls.append(kwargs.get("script_context"))
            return original_detect(text, **kwargs)

        with patch("pageindex_mcp.helpers.garble.detect_garble", side_effect=spy_detect):
            blocks = [
                {"role": "prose", "text": "The quick brown fox jumps over the lazy dog near the river bank. Birds sing loudly in tall oak trees during warm summer mornings. Fresh coffee aroma fills the kitchen as sunlight streams through windows. Cars drive along the highway while pedestrians cross at marked intersections. "},
            ]
            ctx_with_forms = _default_ctx(had_presentation_forms=True)
            _garble_check_flat_blocks(
                blocks,
                script_context=ctx_with_forms,
                config=_default_config(),
            )

        assert len(calls) >= 1
        assert all(c.had_presentation_forms is True for c in calls if c is not None)


# ── FLAT_GATE_COVERAGE exhaustiveness ─────────────────────────────

class TestFlatGateCoverageExhaustiveness:
    """Exhaustiveness: every FLAT-routing TreeDefect has a coverage entry."""

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

    def test_coverage_values_are_callable_names(self):
        for defect, name in FLAT_GATE_COVERAGE.items():
            assert isinstance(name, str)
            assert name, f"Empty callable name for {defect}"


# ── short_text_prior_garble at block granularity ──────────────────

class TestShortTextBlockGranularity:
    """Regression (RFC-025 D2): short_text_prior_garble short-circuit
    fires at block granularity."""

    def test_short_block_skipped_not_counted_garbled(self):
        blocks = [
            {"role": "prose", "text": "Hi"},
            {"role": "prose", "text": "Normal clean text here. " * 30},
        ]
        report = _garble_check_flat_blocks(
            blocks,
            script_context=_default_ctx(),
            config=_default_config(),
        )
        assert report is None


# ── Empty / whitespace blocks ─────────────────────────────────────

class TestEmptyAndWhitespaceBlocks:
    """Contract: empty or whitespace-only blocks are skipped, not counted
    as garbled."""

    def test_empty_blocks_skipped(self):
        blocks = [
            {"role": "prose", "text": ""},
            {"role": "prose", "text": "   \n  "},
            {"role": "prose", "text": "The quick brown fox jumps over the lazy dog near the river bank. Birds sing loudly in tall oak trees during warm summer mornings. Fresh coffee aroma fills the kitchen as sunlight streams through windows. Cars drive along the highway while pedestrians cross at marked intersections safely. "},
        ]
        report = _garble_check_flat_blocks(
            blocks,
            script_context=_default_ctx(),
            config=_default_config(),
        )
        assert report is None

    def test_only_empty_blocks_returns_none(self):
        blocks = [
            {"role": "prose", "text": ""},
            {"role": "prose", "text": ""},
        ]
        report = _garble_check_flat_blocks(
            blocks,
            script_context=_default_ctx(),
            config=_default_config(),
        )
        assert report is None


# ── _flat_block_primary_text for table role ───────────────────────

class TestFlatBlockPrimaryTextTable:
    """Contract: table blocks use row_records for primary text."""

    def test_table_block_uses_row_records(self):
        block = {"role": "table", "text": "", "row_records": ["a|b", "c|d"]}
        assert _flat_block_primary_text(block) == "a|b\nc|d"

    def test_prose_block_uses_text(self):
        block = {"role": "prose", "text": "hello world"}
        assert _flat_block_primary_text(block) == "hello world"


# ── Post-Zone-1 wiring: production call ordering ─────────────────

# ── Zone "Garble Detection Fragmentation" wiring tests ─────────────────────


class TestScriptContextThreadsThroughValidateTree:
    """Wiring: ScriptContext.had_presentation_forms threads through
    validate_tree to _gate_garbling and _gate_node_garbling."""

    def test_had_presentation_forms_threads_to_garble_gate(self):
        from unittest.mock import patch
        from pageindex_mcp.helpers.tree_validation import validate_tree

        # Build a tree with enough content to pass basic gates
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

        # Spy on detect_garble calls to verify had_presentation_forms threading
        calls = []
        from pageindex_mcp.helpers.garble import detect_garble as _orig_detect

        def spy_detect(text, **kwargs):
            sc = kwargs.get("script_context")
            if sc is not None:
                calls.append(sc.had_presentation_forms)
            return _orig_detect(text, **kwargs)

        with patch("pageindex_mcp.helpers.garble.detect_garble", side_effect=spy_detect):
            validate_tree(tree, expected_script=ctx)

        # At least one call should have had_presentation_forms=True
        assert any(c is True for c in calls), (
            f"No detect_garble call received had_presentation_forms=True; "
            f"values seen: {calls}"
        )


class TestApplyPromotionsScriptContextWiring:
    """Wiring: apply_promotions receives and uses ScriptContext instead of
    constructing throwaway ScriptContext(had_presentation_forms=False)."""

    def test_script_context_threaded_to_detect_garble(self):
        from unittest.mock import patch, MagicMock
        from pageindex_mcp.helpers.verdict import apply_promotions
        from pageindex_mcp.helpers.types import GateOutcome, TreeDefect, VerdictThresholds
        from pageindex_mcp.helpers.tree_validation import TreeSignals
        from pageindex_mcp.config import pipeline_config

        text = "clean content " * 50
        tree = [
            {
                "title": "Root",
                "text": text,
                "nodes": [
                    {"title": "A", "text": text, "nodes": []},
                ],
            }
        ]
        sig = TreeSignals.from_tree(tree)
        th = VerdictThresholds.from_config(pipeline_config)

        outcome = GateOutcome(
            defect=TreeDefect.OK,
            validate_reason=None,
            signals=sig,
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
                th=th,
                expected_script="Arab",
                script_context=sc,
            )

        # If detect_garble was called in apply_promotions (image_enrichment path),
        # it should have received had_presentation_forms=True
        if calls:
            assert any(c is True for c in calls), (
                f"apply_promotions called detect_garble without threading "
                f"had_presentation_forms=True; values: {calls}"
            )


class TestEndToEndScriptContextNoThrowaway:
    """Integration: ScriptContext.from_document flows through validate_tree
    and compute_verdict without any had_presentation_forms=False reconstruction
    at key call sites."""

    def test_no_throwaway_script_context_in_tree_signals(self):
        """TreeSignals.from_tree, when given a ScriptContext with
        had_presentation_forms=True, does NOT construct a new ScriptContext
        with had_presentation_forms=False."""
        from unittest.mock import patch
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

        # Track all ScriptContext constructions
        constructed = []
        _orig_init = ScriptContext.__init__

        def spy_init(self, *args, **kwargs):
            _orig_init(self, *args, **kwargs)
            constructed.append(self)

        with patch.object(ScriptContext, "__init__", spy_init):
            TreeSignals.from_tree(tree, expected_script=ctx)

        # Verify that any ScriptContext constructed inside from_tree with
        # source="tree_signals" carries the had_presentation_forms from the
        # original context (True), not a hardcoded False.
        tree_signals_ctxs = [c for c in constructed if c.source == "tree_signals"]
        for c in tree_signals_ctxs:
            assert c.had_presentation_forms is True, (
                f"TreeSignals.from_tree constructed ScriptContext with "
                f"had_presentation_forms=False (source={c.source})"
            )


# ── Post-Zone-1 wiring: production call ordering ─────────────────

class TestPersistFlatResultOrdering:
    """Wiring (blocker #2 resolution): _persist_flat_result call ordering
    after Zone-1 restructuring is:
      splice_figure_markers
      → route_and_extract_flat (block decomposition)
      → _garble_check_flat_blocks (per-block garble gate)
      → _apply_picture_enrichment(splice_markers=False)
        → _enrich_image_blocks (inside _apply_picture_enrichment)
    """

    def test_persist_flat_result_call_ordering(self):
        src = inspect.getsource(
            __import__(
                "pageindex_mcp.client.indexer", fromlist=["CustomPageIndexClient"]
            ).CustomPageIndexClient._persist_flat_result
        )
        src = textwrap.dedent(src)
        tree = ast.parse(src)

        target_names = {
            "splice_figure_markers",
            "route_and_extract_flat",
            "_garble_check_flat_blocks",
            "_apply_picture_enrichment",
        }
        calls_with_line: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in target_names:
                    calls_with_line.append((node.lineno, name))
                for arg in node.args:
                    if isinstance(arg, ast.Name) and arg.id in target_names:
                        calls_with_line.append((arg.lineno, arg.id))

        calls_with_line.sort(key=lambda t: t[0])
        seen: list[str] = []
        seen_set: set[str] = set()
        for _, name in calls_with_line:
            if name not in seen_set:
                seen.append(name)
                seen_set.add(name)

        expected_order = [
            "splice_figure_markers",
            "route_and_extract_flat",
            "_garble_check_flat_blocks",
            "_apply_picture_enrichment",
        ]
        assert seen == expected_order, (
            f"Expected call ordering {expected_order}, got {seen}"
        )
