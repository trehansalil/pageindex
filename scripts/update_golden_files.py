#!/usr/bin/env python3
"""Regenerate golden-file pipeline snapshots.

RFC-041 D6: Run this script after intentional pipeline changes to update
golden-file snapshots.  Each regenerated file should be reviewed in the
PR diff before merge.

Usage:
    uv run python scripts/update_golden_files.py
    uv run python scripts/update_golden_files.py --archetype arabic_garbled
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from pageindex_mcp.helpers import validate_tree
from pageindex_mcp.helpers.garble import _garble_config, detect_garble
from pageindex_mcp.helpers.gates import GATES
from pageindex_mcp.helpers.tree_validation import TreeSignals
from pageindex_mcp.helpers.verdict import compute_verdict
from pageindex_mcp.script import BlobKind, ScriptContext

GOLDEN_DIR = pathlib.Path(__file__).resolve().parent.parent / "tests" / "golden_files"


def _run_pipeline(golden: dict) -> dict:
    inp = golden["input"]
    structure = inp["structure"]
    content_class = inp["content_class"]
    expected_script = inp.get("expected_script")
    image_enrichment_ratio = inp.get("image_enrichment_ratio")
    source_selection = inp.get("source_selection", False)

    sc = ScriptContext(
        dominant_script=expected_script,
        had_presentation_forms=False,
        source="golden_update",
    )

    gate_result = validate_tree(structure, expected_script=sc)
    sig = TreeSignals.from_tree(structure, expected_script=sc)

    garble_report = detect_garble(
        sig.flat_text,
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
        "gate_ok": gate_result.ok,
        "gate_defect": gate_result.defect.value,
        "verdict": verdict_result.verdict,
        "verdict_reason": verdict_result.reason,
        "recovery_eligible": recovery_eligible,
        "recovery_method": recovery_method,
    }


def update_golden_file(path: pathlib.Path) -> dict:
    with open(path) as f:
        golden = json.load(f)

    observed = _run_pipeline(golden)

    new_expected = dict(golden["expected"])
    new_expected["garble_detected"] = observed["garble_detected"]
    new_expected["gate_ok"] = observed["gate_ok"]
    new_expected["recovery_eligible"] = observed["recovery_eligible"]

    if "gate_defect" in new_expected:
        new_expected["gate_defect"] = observed["gate_defect"]
    if "gate_defect_in" in new_expected:
        if observed["gate_defect"] not in new_expected["gate_defect_in"]:
            new_expected["gate_defect_in"].append(observed["gate_defect"])

    if "verdict" in new_expected:
        new_expected["verdict"] = observed["verdict"]
    if "verdict_in" in new_expected:
        if observed["verdict"] not in new_expected["verdict_in"]:
            new_expected["verdict_in"].append(observed["verdict"])

    if "verdict_reason_prefix" in new_expected:
        reason = observed["verdict_reason"]
        paren = reason.find("(")
        eq = reason.find("=")
        cut = min(x for x in [paren, eq, len(reason)] if x >= 0)
        new_expected["verdict_reason_prefix"] = reason[:cut]

    if "recovery_method" in new_expected:
        new_expected["recovery_method"] = observed["recovery_method"]

    golden["expected"] = new_expected

    with open(path, "w") as f:
        json.dump(golden, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return observed


def main() -> None:
    parser = argparse.ArgumentParser(description="Update golden-file pipeline snapshots")
    parser.add_argument(
        "--archetype",
        help="Update only this archetype (filename without .json)",
    )
    args = parser.parse_args()

    files = sorted(GOLDEN_DIR.glob("*.json"))
    if args.archetype:
        files = [f for f in files if f.stem == args.archetype]
        if not files:
            print(f"No golden file found for archetype: {args.archetype}")
            sys.exit(1)

    for path in files:
        observed = update_golden_file(path)
        print(
            f"Updated {path.name}: "
            f"verdict={observed['verdict']}, "
            f"garble={observed['garble_detected']}, "
            f"gate_ok={observed['gate_ok']}"
        )

    print(f"\nUpdated {len(files)} golden file(s). Review diffs before committing.")


if __name__ == "__main__":
    main()
