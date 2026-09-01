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
    VERDICT_PRIORITY,
    TreeDefect,
    VerdictThresholds,
)
from pageindex_mcp.helpers.verdict import (
    apply_promotions,
    compute_verdict,
    evaluate_gates,
)
from pageindex_mcp.script import BlobKind, ScriptContext

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

    from pageindex_mcp.helpers.gates import GATES, REASON_POLICY
    from pageindex_mcp.helpers.types import _ReasonPolicy

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
    """Golden-file pipeline snapshot tests (RFC-041 D6)."""

    @pytest.mark.parametrize("name,golden", GOLDEN_CASES, ids=GOLDEN_IDS)
    def test_garble_detection(self, name: str, golden: dict) -> None:
        observed = _run_triad_pipeline(golden)
        expected = golden["expected"]
        assert observed["garble_detected"] == expected["garble_detected"], (
            f"[{name}] garble_detected mismatch: "
            f"expected={expected['garble_detected']}, got={observed['garble_detected']}"
        )

    @pytest.mark.parametrize("name,golden", GOLDEN_CASES, ids=GOLDEN_IDS)
    def test_gate_result(self, name: str, golden: dict) -> None:
        observed = _run_triad_pipeline(golden)
        expected = golden["expected"]
        assert observed["gate_ok"] == expected["gate_ok"], (
            f"[{name}] gate_ok mismatch: "
            f"expected={expected['gate_ok']}, got={observed['gate_ok']}"
        )
        if "gate_defect" in expected:
            assert observed["gate_defect"] == expected["gate_defect"], (
                f"[{name}] gate_defect mismatch: "
                f"expected={expected['gate_defect']}, got={observed['gate_defect']}"
            )
        if "gate_defect_in" in expected:
            assert observed["gate_defect"] in expected["gate_defect_in"], (
                f"[{name}] gate_defect not in expected set: "
                f"expected one of {expected['gate_defect_in']}, got={observed['gate_defect']}"
            )

    @pytest.mark.parametrize("name,golden", GOLDEN_CASES, ids=GOLDEN_IDS)
    def test_verdict(self, name: str, golden: dict) -> None:
        observed = _run_triad_pipeline(golden)
        expected = golden["expected"]
        if "verdict" in expected:
            assert observed["verdict"] == expected["verdict"], (
                f"[{name}] verdict mismatch: "
                f"expected={expected['verdict']}, got={observed['verdict']} "
                f"(reason={observed['verdict_reason']})"
            )
        if "verdict_in" in expected:
            assert observed["verdict"] in expected["verdict_in"], (
                f"[{name}] verdict not in expected set: "
                f"expected one of {expected['verdict_in']}, got={observed['verdict']} "
                f"(reason={observed['verdict_reason']})"
            )
        if "verdict_reason_prefix" in expected:
            assert observed["verdict_reason"].startswith(expected["verdict_reason_prefix"]), (
                f"[{name}] verdict_reason prefix mismatch: "
                f"expected prefix={expected['verdict_reason_prefix']!r}, "
                f"got={observed['verdict_reason']!r}"
            )

    @pytest.mark.parametrize("name,golden", GOLDEN_CASES, ids=GOLDEN_IDS)
    def test_recovery_eligibility(self, name: str, golden: dict) -> None:
        observed = _run_triad_pipeline(golden)
        expected = golden["expected"]
        assert observed["recovery_eligible"] == expected["recovery_eligible"], (
            f"[{name}] recovery_eligible mismatch: "
            f"expected={expected['recovery_eligible']}, got={observed['recovery_eligible']}"
        )

    @pytest.mark.parametrize("name,golden", GOLDEN_CASES, ids=GOLDEN_IDS)
    def test_full_snapshot(self, name: str, golden: dict) -> None:
        """Full pipeline snapshot: any shift in any field is a visible diff."""
        observed = _run_triad_pipeline(golden)
        expected = golden["expected"]

        failures = []
        for key in ["garble_detected", "gate_ok", "recovery_eligible"]:
            if observed[key] != expected[key]:
                failures.append(f"  {key}: expected={expected[key]}, got={observed[key]}")

        if "verdict" in expected and observed["verdict"] != expected["verdict"]:
            failures.append(
                f"  verdict: expected={expected['verdict']}, got={observed['verdict']}"
            )
        if "verdict_in" in expected and observed["verdict"] not in expected["verdict_in"]:
            failures.append(
                f"  verdict: expected one of {expected['verdict_in']}, got={observed['verdict']}"
            )

        if "gate_defect" in expected and observed["gate_defect"] != expected["gate_defect"]:
            failures.append(
                f"  gate_defect: expected={expected['gate_defect']}, got={observed['gate_defect']}"
            )

        if failures:
            pytest.fail(
                f"Golden-file snapshot diff for [{name}]:\n" + "\n".join(failures)
            )


class TestGoldenFileIntegrity:
    """Meta-tests ensuring the golden-file suite itself is valid."""

    def test_minimum_archetype_count(self) -> None:
        assert len(GOLDEN_CASES) >= 8, (
            f"RFC-041 D6 requires 8-12 archetypes, found {len(GOLDEN_CASES)}"
        )

    def test_unique_archetype_names(self) -> None:
        names = [name for name, _ in GOLDEN_CASES]
        assert len(set(names)) == len(names), (
            f"Duplicate archetype names: {[n for n in names if names.count(n) > 1]}"
        )

    def test_all_golden_files_have_required_fields(self) -> None:
        required_input = {"structure", "content_class"}
        required_expected = {"garble_detected", "gate_ok", "recovery_eligible"}
        for name, golden in GOLDEN_CASES:
            inp_keys = set(golden["input"].keys())
            assert required_input.issubset(inp_keys), (
                f"[{name}] missing input fields: {required_input - inp_keys}"
            )
            exp_keys = set(golden["expected"].keys())
            assert required_expected.issubset(exp_keys), (
                f"[{name}] missing expected fields: {required_expected - exp_keys}"
            )

    def test_verdict_field_present(self) -> None:
        for name, golden in GOLDEN_CASES:
            exp = golden["expected"]
            assert "verdict" in exp or "verdict_in" in exp, (
                f"[{name}] must have either 'verdict' or 'verdict_in' in expected"
            )
