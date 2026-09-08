# ALLOW-NEW-TEST-FILE: RFC-045 R2 facade disposition measurement regression guard
"""RFC-045 Requirement 2: facade disposition measurement regression guard.

``scripts/facade_surface_measure.py`` is the reproducible artifact behind
RFC-045's disposition rule. Its headline claim -- that a *narrow* coupling
rule preserves the removal set (48 of 59 survive) while a *broad*
"referenced anywhere in a kept sibling" rule collapses it (10 of 59), which
is what sank the earlier 59 -> 7 proposal -- is the evidence the whole
requirement rests on. These tests pin that claim so a silent change in the
signal battery, the manifest, or the source it reads cannot rewrite the
RFC's numbers without a visible failure.

The pinned numbers describe the PRE-SHRINK facade. Once RFC-045 execution
starts removing entries, ``__all__`` and the ``__init__.py`` import blocks
change under the measurement's feet, so the reproduction test skips itself
and says so rather than failing confusingly mid-wave. Updating the pins is
an explicit task in ``agents/tasks/tasks-rfc045-package-facade-surface.md``.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "facade_surface_measure.py"
_spec = importlib.util.spec_from_file_location("facade_surface_measure", _SCRIPT)
measure_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure_mod)


#: Section B's candidate population. A manifest edit that changes this is a
#: different measurement and must not silently reuse RFC-045 R2's numbers.
EXPECTED_CANDIDATES = 59

#: Under the narrow rule, this many of the 59 survive as REMOVE.
EXPECTED_NARROW_REMOVE = 48

#: Under the broad rule, this many survive -- the collapse the RFC rejects.
EXPECTED_BROAD_REMOVE = 10

#: The names the narrow rule flips REMOVE -> KEEP, with the signal that
#: caught each. These are exactly the candidates RFC-045 R2 sends to hand
#: review; a name entering or leaving this set changes what a human must rule
#: on, so it may not drift silently.
EXPECTED_FLIPS = {
    ("client", "RecoveryMixin"): ["string_registry"],
    ("converters", "FuturesTimeoutError"): ["string_registry"],
    ("converters", "ScriptContext"): ["type_annotation", "string_registry"],
    ("converters", "StageRecord"): ["type_annotation"],
    ("converters", "_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S"): ["arithmetic_operand"],
    ("converters", "_D7_FITZ_FALLBACK_ENABLED"): ["flag_gate"],
    ("converters", "_PAGE_ROTATION_DETECTION_ENABLED"): ["flag_gate"],
    ("converters", "_pdf_inspector_available"): ["flag_gate"],
    ("helpers", "_GateFn"): ["type_annotation"],
    ("worker", "_mirror_bridged_incr"): ["cross_package"],
    ("worker", "_mirror_bridged_set"): ["cross_package"],
}

#: Per-package REMOVE counts under the narrow rule, so a package-wide
#: regression cannot hide inside a correct grand total.
EXPECTED_PER_PACKAGE_REMOVE = {
    "client": 2,
    "converters": 19,
    "helpers": 11,
    "registry_backfill": 8,
    "storage": 1,
    "worker": 7,
}


def _facade_is_unshrunk(removals) -> bool:
    """True while every candidate is still exported by its package."""
    for pkg in {p for p, _ in removals}:
        all_names, _blocks = measure_mod.parse_init(pkg)
        if any(name not in all_names for p, name in removals if p == pkg):
            return False
    return True


@pytest.fixture(scope="module")
def removals():
    return measure_mod.load_removals()


@pytest.fixture(scope="module")
def results(removals):
    if not _facade_is_unshrunk(removals):
        pytest.skip(
            "RFC-045 shrink has started: the facade no longer matches the "
            "pre-shrink source these numbers were measured against. Re-run "
            "scripts/facade_surface_measure.py and update the pins in this "
            "file (see tasks-rfc045-package-facade-surface.md)."
        )
    return measure_mod.measure(removals)


def test_manifest_section_b_lists_the_pinned_candidate_population(removals):
    """The 59-candidate population is what R2's disposition rule was measured on."""
    # Arrange / Act done by the fixture.
    # Assert
    assert len(removals) == EXPECTED_CANDIDATES
    assert len(set(removals)) == EXPECTED_CANDIDATES, "section B has duplicate rows"


def test_narrow_rule_preserves_the_removal_set(results):
    """48 of 59 survive as REMOVE -- the number RFC-045 R2 commits to."""
    # Arrange / Act
    remove = [r for r in results if r["narrow_disposition"] == "REMOVE"]

    # Assert
    assert len(remove) == EXPECTED_NARROW_REMOVE


def test_broad_rule_collapses_the_removal_set(results):
    """The broad rule reproduces the 59 -> single-digit collapse the RFC rejects.

    This is the comparison that justifies choosing the narrow rule; without it
    "narrow" is an unmotivated preference rather than a measured one.
    """
    # Arrange / Act
    remove = [r for r in results if r["broad_disposition"] == "REMOVE"]

    # Assert
    assert len(remove) == EXPECTED_BROAD_REMOVE
    assert len(remove) < EXPECTED_NARROW_REMOVE


def test_hand_review_set_is_exactly_the_pinned_eleven(results):
    """The names sent to human ruling, and the signal that caught each."""
    # Arrange / Act
    flips = {
        (r["package"], r["name"]): r["narrow_reasons"]
        for r in results
        if r["narrow_disposition"] == "KEEP"
    }

    # Assert
    assert set(flips) == set(EXPECTED_FLIPS)
    for key, reasons in EXPECTED_FLIPS.items():
        assert flips[key] == reasons, f"{key} is now caught by a different signal"


def test_per_package_removal_counts_are_stable(results):
    """A package-wide regression must not hide inside a correct grand total."""
    # Arrange / Act
    counts: dict[str, int] = {}
    for row in results:
        if row["narrow_disposition"] == "REMOVE":
            counts[row["package"]] = counts.get(row["package"], 0) + 1

    # Assert
    assert counts == EXPECTED_PER_PACKAGE_REMOVE
    assert sum(counts.values()) == EXPECTED_NARROW_REMOVE
