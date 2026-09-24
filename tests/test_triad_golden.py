# ALLOW-NEW-TEST-FILE: RFC-041 D6 golden-file pipeline snapshot tests
"""Golden-file pipeline snapshot tests for the verdict/garble/recovery triad.

RFC-041 D6: 10 canonical document archetypes with full pipeline snapshots.
Any code change shifting a verdict produces a visible diff.  Use
``scripts/update_golden_files.py`` for intentional snapshot regeneration.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from pageindex_mcp.client.recovery import _keep_best_wins
from pageindex_mcp.helpers import (
    TreeGateResult,
    validate_tree,
)
from pageindex_mcp.helpers.garble import (
    GarbleConfig,
    _garble_config,
    detect_garble,
)
from pageindex_mcp.helpers.tree_validation import TreeSignals
from pageindex_mcp.helpers.types import (
    TreeDefect,
)
from pageindex_mcp.helpers.verdict import (
    compute_verdict,
)
from pageindex_mcp.script import BlobKind, ScriptContext
from tests.conftest import filler_text

GOLDEN_DIR = pathlib.Path(__file__).parent / "golden_files"


def _load_golden_files() -> list[tuple[str, dict]]:
    files = sorted(GOLDEN_DIR.glob("*.json"))
    result = []
    for f in files:
        with open(f) as fh:
            data = json.load(fh)
        result.append((data["archetype"], data))
    return result


GOLDEN_CASES = _load_golden_files()
GOLDEN_IDS = [name for name, _ in GOLDEN_CASES]


def _run_triad_pipeline(golden: dict) -> dict:
    """Run the full triad pipeline on a golden-file input and return observed results."""
    inp = golden["input"]
    structure = inp["structure"]
    content_class = inp["content_class"]
    expected_script = inp.get("expected_script")
    image_enrichment_ratio = inp.get("image_enrichment_ratio")
    source_selection = inp.get("source_selection", False)

    sc = ScriptContext(
        dominant_script=expected_script,
        had_presentation_forms=False,
        source="golden_test",
    )

    gate_result = validate_tree(structure, expected_script=sc)

    flat_text = TreeSignals.from_tree(structure, expected_script=sc).flat_text

    garble_report = detect_garble(
        flat_text,
        script_context=sc,
        config=_garble_config,
        blob_kind=BlobKind.TREE_TEXT,
    )

    verdict_result = compute_verdict(
        structure,
        content_class,
        validate_result=gate_result,
        image_enrichment_ratio=image_enrichment_ratio,
        expected_script=sc,
        source_selection=source_selection,
    )

    from pageindex_mcp.helpers.gates import GATES

    recovery_eligible = False
    recovery_method = None
    if not gate_result.ok:
        for g in GATES:
            if g.defect == gate_result.defect and g.recovery_fns:
                recovery_eligible = True
                recovery_method = g.recovery_fns[0] if g.recovery_fns else None
                break

    return {
        "garble_detected": garble_report.is_garbled,
        "garble_ratio": garble_report.garble_ratio,
        "gate_ok": gate_result.ok,
        "gate_defect": gate_result.defect.value,
        "verdict": verdict_result.verdict,
        "verdict_reason": verdict_result.reason,
        "recovery_eligible": recovery_eligible,
        "recovery_method": recovery_method,
    }


class TestTriadGoldenFiles:
    """Golden-file pipeline snapshot tests (RFC-041 D6).

    One collected test drives every archetype.  Each golden file's full
    snapshot (garble / gate / verdict / recovery eligibility) is compared
    and *every* mismatching field of *every* archetype is reported, so a
    single run still names each offending row.
    """

    def test_full_pipeline_snapshot_all_archetypes(self) -> None:
        failures: list[str] = []

        for name, golden in GOLDEN_CASES:
            observed = _run_triad_pipeline(golden)
            expected = golden["expected"]

            for key in ("garble_detected", "gate_ok", "recovery_eligible"):
                if observed[key] != expected[key]:
                    failures.append(
                        f"  [{name}] {key}: expected={expected[key]}, got={observed[key]}"
                    )

            if "gate_defect" in expected and observed["gate_defect"] != expected["gate_defect"]:
                failures.append(
                    f"  [{name}] gate_defect: expected={expected['gate_defect']}, "
                    f"got={observed['gate_defect']}"
                )
            if (
                "gate_defect_in" in expected
                and observed["gate_defect"] not in expected["gate_defect_in"]
            ):
                failures.append(
                    f"  [{name}] gate_defect: expected one of {expected['gate_defect_in']}, "
                    f"got={observed['gate_defect']}"
                )

            if "verdict" in expected and observed["verdict"] != expected["verdict"]:
                failures.append(
                    f"  [{name}] verdict: expected={expected['verdict']}, "
                    f"got={observed['verdict']} (reason={observed['verdict_reason']})"
                )
            if "verdict_in" in expected and observed["verdict"] not in expected["verdict_in"]:
                failures.append(
                    f"  [{name}] verdict: expected one of {expected['verdict_in']}, "
                    f"got={observed['verdict']} (reason={observed['verdict_reason']})"
                )
            if "verdict_reason_prefix" in expected and not observed["verdict_reason"].startswith(
                expected["verdict_reason_prefix"]
            ):
                failures.append(
                    f"  [{name}] verdict_reason: expected prefix="
                    f"{expected['verdict_reason_prefix']!r}, got={observed['verdict_reason']!r}"
                )

        if failures:
            pytest.fail(
                f"Golden-file snapshot diff across {len(GOLDEN_CASES)} archetypes:\n"
                + "\n".join(failures)
            )


class TestGoldenFileIntegrity:
    """Meta-test ensuring the golden-file suite itself is valid."""

    def test_golden_suite_is_well_formed(self) -> None:
        required_input = {"structure", "content_class"}
        required_expected = {"garble_detected", "gate_ok", "recovery_eligible"}

        problems: list[str] = []

        if len(GOLDEN_CASES) < 8:
            problems.append(f"RFC-041 D6 requires 8-12 archetypes, found {len(GOLDEN_CASES)}")

        names = [name for name, _ in GOLDEN_CASES]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            problems.append(f"duplicate archetype names: {dupes}")

        for name, golden in GOLDEN_CASES:
            missing_in = required_input - set(golden["input"].keys())
            if missing_in:
                problems.append(f"[{name}] missing input fields: {sorted(missing_in)}")
            exp = golden["expected"]
            missing_exp = required_expected - set(exp.keys())
            if missing_exp:
                problems.append(f"[{name}] missing expected fields: {sorted(missing_exp)}")
            if "verdict" not in exp and "verdict_in" not in exp:
                problems.append(f"[{name}] must have either 'verdict' or 'verdict_in' in expected")

        assert not problems, "Golden-file suite is malformed:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# RFC-041 D7: property-based triad invariants (merged from
# tests/test_triad_properties.py).  Hypothesis runtime is load-dependent, so
# every @settings here carries deadline=None -- a wall-clock deadline would
# flake under full-suite/coverage load without signalling any logic defect.
# ---------------------------------------------------------------------------

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

    @settings(
        max_examples=CI_MAX_EXAMPLES,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
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


class TestKeepBestWinsNeverReverts:
    """Property 7: _keep_best_wins never reverts objectively better retries.

    When post-retry has strictly more chars and is not garbled, the
    function must return True (keep the retry result).
    """

    @settings(
        max_examples=CI_MAX_EXAMPLES,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
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

    @settings(
        max_examples=CI_MAX_EXAMPLES,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
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

    @settings(
        max_examples=CI_MAX_EXAMPLES,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
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
