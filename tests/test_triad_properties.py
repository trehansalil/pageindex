# ALLOW-NEW-TEST-FILE: RFC-041 D7 property-based triad tests
"""Property-based tests for the verdict/garble/recovery triad.

RFC-041 D7: Hypothesis strategies for TreeGateResult, GarbleConfig,
ScriptContext, BlobKind.  Verifies cross-component invariants that
golden-file tests cannot cover exhaustively.

CI configuration: max_examples=200
# Nightly configuration: max_examples=10000
"""
from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, given, settings, assume
from hypothesis import strategies as st

from pageindex_mcp.helpers import (
    TreeGateResult,
    validate_tree,
)
from pageindex_mcp.helpers.garble import (
    GarbleConfig,
    GarbleReport,
    _garble_config,
    detect_garble,
)
from pageindex_mcp.helpers.tree_validation import TreeSignals
from pageindex_mcp.helpers.types import (
    VERDICT_PRIORITY,
    GateOutcome,
    TreeDefect,
    VerdictResult,
    VerdictThresholds,
)
from pageindex_mcp.helpers.verdict import (
    apply_promotions,
    compute_verdict,
    evaluate_gates,
)
from pageindex_mcp.client.recovery import _keep_best_wins
from pageindex_mcp.script import BlobKind, ScriptContext
from tests.conftest import filler_text


CI_MAX_EXAMPLES = 200


_ACTIVE_DEFECTS = [d for d in TreeDefect if d != TreeDefect.ARABIC_LOW_CONTENT_RATIO]

st_tree_defect = st.sampled_from(_ACTIVE_DEFECTS)

st_garble_config = st.builds(
    GarbleConfig,
    garble_latin_gibberish_enabled=st.booleans(),
    garble_latin_ratio=st.floats(min_value=0.1, max_value=0.9),
    garble_nonsense_ratio=st.floats(min_value=0.3, max_value=0.95),
    garble_short_text_default=st.booleans(),
    garble_flat_markdown_normalize=st.booleans(),
    garble_node_ratio_threshold=st.floats(min_value=0.01, max_value=0.5),
    garble_digit_floor=st.integers(min_value=50, max_value=2000),
)

st_script_context = st.builds(
    ScriptContext,
    dominant_script=st.sampled_from([None, "Latn", "Arab"]),
    had_presentation_forms=st.booleans(),
    source=st.sampled_from(["filename", "text_inference", "combined", "none", "golden_test"]),
)

st_blob_kind = st.sampled_from(list(BlobKind))


def _make_structure(
    node_count: int,
    depth: int,
    chars_per_node: int,
    seed: int = 0,
) -> list:
    """Build a synthetic tree structure with given parameters."""
    nodes = []
    for i in range(node_count):
        text = filler_text(chars_per_node, seed + i)
        nodes.append({"title": f"Node{i}", "text": text, "nodes": []})
    if depth >= 2 and nodes:
        return [{"title": "Root", "text": "", "nodes": nodes}]
    return nodes


st_tree_gate_result = st.builds(
    TreeGateResult,
    ok=st.booleans(),
    defect=st_tree_defect,
    detail=st.text(min_size=0, max_size=20),
    signals=st.none(),
    all_defects=st.frozensets(st_tree_defect, max_size=4),
    warnings=st.tuples(),
)


@st.composite
def st_well_formed_structure(draw):
    """Generate structures that satisfy minimum node/depth requirements."""
    node_count = draw(st.integers(min_value=3, max_value=10))
    chars = draw(st.integers(min_value=100, max_value=500))
    seed = draw(st.integers(min_value=0, max_value=1000))
    return _make_structure(node_count, depth=2, chars_per_node=chars, seed=seed)


@st.composite
def st_garbled_text(draw):
    """Generate text that triggers garble detection."""
    garble_chars = "".join(chr(c) for c in range(0xE000, 0xE050))
    length = draw(st.integers(min_value=200, max_value=800))
    ratio = draw(st.floats(min_value=0.5, max_value=0.9))
    garble_len = int(length * ratio)
    normal_len = length - garble_len
    garble_part = (garble_chars * (garble_len // len(garble_chars) + 1))[:garble_len]
    normal_part = filler_text(normal_len, draw(st.integers(min_value=0, max_value=100)))
    return garble_part + " " + normal_part


class TestGarbleConvergenceAcrossPaths:
    """Property 6a: garble detection converges across all paths.

    For any document, all garble detection paths (per-node, per-block,
    whole-tree fallback) SHALL produce the same result as calling
    detect_garble directly on the same text.
    """

    @settings(max_examples=CI_MAX_EXAMPLES, suppress_health_check=[HealthCheck.too_slow])
    @given(
        structure=st_well_formed_structure(),
        script_ctx=st_script_context,
    )
    def test_garble_convergence_across_paths(
        self, structure: list, script_ctx: ScriptContext
    ) -> None:
        sig = TreeSignals.from_tree(structure, expected_script=script_ctx)

        direct_garble = detect_garble(
            sig.flat_text,
            script_context=script_ctx,
            config=_garble_config,
            blob_kind=BlobKind.TREE_TEXT,
        )

        assert sig.garbled == direct_garble.is_garbled, (
            f"TreeSignals.garbled ({sig.garbled}) disagrees with "
            f"detect_garble ({direct_garble.is_garbled}) on same flat_text"
        )


class TestGarbleNeverPasses:
    """Property 6b: garble detected => never PASS.

    xfail until D5 expiry: source_selection bypass at verdict.py:479
    currently grants unconditional PASS, violating this property.
    """

    @pytest.mark.xfail(
        reason="source_selection bypass at verdict.py:479 violates this pre-D5 expiry",
        strict=False,
    )
    @settings(max_examples=CI_MAX_EXAMPLES, suppress_health_check=[HealthCheck.too_slow])
    @given(
        structure=st_well_formed_structure(),
        script_ctx=st_script_context,
        source_selection=st.booleans(),
        image_enrichment_ratio=st.one_of(
            st.none(),
            st.floats(min_value=0.0, max_value=1.0),
        ),
    )
    def test_garble_never_passes(
        self,
        structure: list,
        script_ctx: ScriptContext,
        source_selection: bool,
        image_enrichment_ratio: float | None,
    ) -> None:
        gate_result = validate_tree(structure, expected_script=script_ctx)
        sig = TreeSignals.from_tree(structure, expected_script=script_ctx)

        assume(sig.effectively_garbled)

        verdict_result = compute_verdict(
            structure,
            "text",
            validate_result=gate_result,
            image_enrichment_ratio=image_enrichment_ratio,
            expected_script=script_ctx,
            source_selection=source_selection,
        )

        assert verdict_result.verdict != "PASS", (
            f"Garbled document (effectively_garbled=True) got PASS verdict "
            f"(reason={verdict_result.reason}, source_selection={source_selection})"
        )


class TestKeepBestWinsNeverReverts:
    """Property 7: _keep_best_wins never reverts objectively better retries.

    When post-retry has strictly more chars and is not garbled, the
    function must return True (keep the retry result).
    """

    @settings(max_examples=CI_MAX_EXAMPLES, suppress_health_check=[HealthCheck.too_slow])
    @given(
        pre_chars=st.integers(min_value=100, max_value=5000),
        extra_chars=st.integers(min_value=50, max_value=2000),
        seed=st.integers(min_value=0, max_value=1000),
    )
    def test_keep_best_wins_keeps_objectively_better(
        self,
        pre_chars: int,
        extra_chars: int,
        seed: int,
    ) -> None:
        post_chars = pre_chars + extra_chars
        pre_text = filler_text(pre_chars, seed)
        post_text = filler_text(post_chars, seed + 1)

        pre_structure = [{"title": "Pre", "text": pre_text, "nodes": []}]
        post_structure = [{"title": "Post", "text": post_text, "nodes": []}]

        pre_result = {"structure": pre_structure}
        post_result = {"structure": post_structure}

        sc = ScriptContext(
            dominant_script=None,
            had_presentation_forms=False,
            source="test",
        )

        kept = _keep_best_wins(
            pre_result=pre_result,
            pre_total_chars=pre_chars,
            post_result=post_result,
            post_ok=True,
            expected_script=None,
            script_context=sc,
            filename="test.pdf",
        )

        assert kept is True, (
            f"_keep_best_wins reverted an objectively better retry: "
            f"pre_chars={pre_chars}, post_chars={post_chars}"
        )


class TestNoopRecoveryPreservesVerdict:
    """Property 7: no-op recovery preserves PASS.

    When a document already has a PASS verdict and recovery produces
    no change (_keep_best_wins returns False), the verdict after
    recovery must remain PASS.
    """

    @settings(max_examples=CI_MAX_EXAMPLES, suppress_health_check=[HealthCheck.too_slow])
    @given(
        node_count=st.integers(min_value=4, max_value=8),
        chars_per_node=st.integers(min_value=200, max_value=600),
        seed=st.integers(min_value=0, max_value=1000),
    )
    def test_noop_recovery_preserves_verdict(
        self,
        node_count: int,
        chars_per_node: int,
        seed: int,
    ) -> None:
        structure = _make_structure(node_count, depth=2, chars_per_node=chars_per_node, seed=seed)

        sc = ScriptContext(
            dominant_script=None,
            had_presentation_forms=False,
            source="test",
        )

        pre_verdict = compute_verdict(
            structure,
            "text",
            validate_result=validate_tree(structure, expected_script=sc),
            expected_script=sc,
        )

        assume(pre_verdict.verdict == "PASS")

        flat_text = TreeSignals.from_tree(structure, expected_script=sc).flat_text
        pre_total_chars = len(flat_text)
        pre_result = {"structure": structure}
        post_result = {"structure": structure}

        kept = _keep_best_wins(
            pre_result=pre_result,
            pre_total_chars=pre_total_chars,
            post_result=post_result,
            post_ok=True,
            expected_script=None,
            script_context=sc,
            filename="test.pdf",
        )

        if not kept:
            post_verdict = compute_verdict(
                structure,
                "text",
                validate_result=validate_tree(structure, expected_script=sc),
                expected_script=sc,
            )
            assert post_verdict.verdict == "PASS", (
                f"No-op recovery changed verdict from PASS to {post_verdict.verdict} "
                f"(reason={post_verdict.reason})"
            )


class TestKeepBestWinsCharRegression:
    """Property 7 extension: _keep_best_wins never keeps a result
    with strictly fewer characters than pre-retry.

    Note: _keep_best_wins uses _flatten_tree_text internally which includes
    title text and newline separators.  pre_total_chars must reflect the
    actual flattened length to avoid measurement mismatches.
    """

    @settings(max_examples=CI_MAX_EXAMPLES, suppress_health_check=[HealthCheck.too_slow])
    @given(
        pre_chars=st.integers(min_value=200, max_value=5000),
        reduction=st.integers(min_value=50, max_value=1000),
        seed=st.integers(min_value=0, max_value=1000),
    )
    def test_keep_best_wins_rejects_char_regression(
        self,
        pre_chars: int,
        reduction: int,
        seed: int,
    ) -> None:
        from pageindex_mcp.helpers.tree_validation import _flatten_tree_text

        post_chars = max(10, pre_chars - reduction)
        assume(post_chars < pre_chars)

        pre_text = filler_text(pre_chars, seed)
        post_text = filler_text(post_chars, seed + 1)

        pre_structure = [{"title": "", "text": pre_text, "nodes": []}]
        post_structure = [{"title": "", "text": post_text, "nodes": []}]

        actual_pre_total = len(_flatten_tree_text(pre_structure))

        sc = ScriptContext(
            dominant_script=None,
            had_presentation_forms=False,
            source="test",
        )

        kept = _keep_best_wins(
            pre_result={"structure": pre_structure},
            pre_total_chars=actual_pre_total,
            post_result={"structure": post_structure},
            post_ok=True,
            expected_script=None,
            script_context=sc,
            filename="test.pdf",
        )

        assert kept is False, (
            f"_keep_best_wins kept a result with fewer chars: "
            f"pre_total={actual_pre_total}, post_text_len={post_chars}"
        )
