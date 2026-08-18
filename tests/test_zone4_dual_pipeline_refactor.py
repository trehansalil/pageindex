"""Zone 4: Dual pipeline structural refactor — Candidate dataclass, single entry point.

Covers:
  - Candidate dataclass is frozen with md + heading_pages fields
  - _candidate_from_document builds Candidate via _build_candidate + _recover_heading_depth
  - _run_stages returns dict[str, dict] keyed by stage name (not list)
  - _has_structural_depth exists and replaces _has_recoverable_structure
"""

import dataclasses

import pytest

from pageindex_mcp.converters import (
    Candidate,
    _build_candidate,
    _candidate_from_document,
    _has_structural_depth,
    _run_stages,
)


class TestCandidateDataclass:
    """Candidate is a frozen dataclass bundling md + heading_pages."""

    def test_candidate_is_frozen(self):
        c = Candidate(md="# Title", heading_pages={})
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.md = "changed"  # type: ignore[misc]

    def test_candidate_fields(self):
        hp = {"# Intro": [1, 2]}
        c = Candidate(md="# Intro\n\nBody.", heading_pages=hp)
        assert c.md == "# Intro\n\nBody."
        assert c.heading_pages == hp

    def test_candidate_heading_pages_default_empty(self):
        c = Candidate(md="hello")
        assert c.heading_pages == {}

    def test_candidate_equality(self):
        a = Candidate(md="x", heading_pages={"# A": [1]})
        b = Candidate(md="x", heading_pages={"# A": [1]})
        assert a == b

    def test_candidate_inequality_on_md(self):
        a = Candidate(md="x")
        b = Candidate(md="y")
        assert a != b

    def test_candidate_inequality_on_heading_pages(self):
        a = Candidate(md="x", heading_pages={"# A": [1]})
        b = Candidate(md="x", heading_pages={"# B": [2]})
        assert a != b


class TestCandidateFromDocument:
    """_candidate_from_document is the single entry point for building a
    pipeline candidate (replaces duplicated _build_candidate +
    _recover_heading_depth calls)."""

    def test_returns_candidate_instance(self):
        result = _candidate_from_document("# Title\n\nBody.", {}, "/fake.pdf")
        assert isinstance(result, Candidate)

    def test_preserves_heading_pages(self):
        hp = {"# Title": [0]}
        result = _candidate_from_document("# Title\n\nBody.", hp, "/fake.pdf")
        assert result.heading_pages is hp

    def test_applies_build_candidate(self):
        """The returned md should have _build_candidate applied (injection +
        normalisation), so it should equal _build_candidate output for simple
        inputs that do not trigger depth recovery."""
        md = "# Simple\n\nPlain text."
        result = _candidate_from_document(md, {}, "/fake.pdf")
        assert result.md == _build_candidate(md)[0]

    def test_result_is_frozen(self):
        result = _candidate_from_document("# X", {}, "/fake.pdf")
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.md = "changed"  # type: ignore[misc]


class TestRunStagesDictReturn:
    """_run_stages returns dict[str, dict] keyed by stage name."""

    def test_return_type_is_dict(self):
        md, records = _run_stages("hello", [("noop", lambda m: m)])
        assert isinstance(records, dict)

    def test_keys_are_stage_names(self):
        def add(md):
            return md + "!"

        md, records = _run_stages("hi", [("exclaim", add), ("double", lambda m: m + m)])
        assert set(records.keys()) == {"exclaim", "double"}

    def test_dict_preserves_insertion_order(self):
        stages = [
            ("alpha", lambda m: m),
            ("beta", lambda m: m),
            ("gamma", lambda m: m),
        ]
        _, records = _run_stages("x", stages)
        assert list(records.keys()) == ["alpha", "beta", "gamma"]

    def test_error_stage_keyed_by_name(self):
        def boom(md):
            raise RuntimeError("fail")

        _, records = _run_stages("x", [("explode", boom)])
        assert "explode" in records
        assert records["explode"]["error"] == "fail"


class TestHasStructuralDepthRename:
    """_has_structural_depth exists and old name is gone."""

    def test_callable(self):
        assert callable(_has_structural_depth)

    def test_old_name_removed(self):
        import pageindex_mcp.converters as mod
        assert not hasattr(mod, "_has_recoverable_structure")

    def test_returns_true_for_deep_tree(self):
        md = "# Title\n\n## Section A\n\n## Section B\n\n## Section C"
        assert _has_structural_depth(md) is True

    def test_returns_false_for_flat_tree(self):
        md = "# Only one heading"
        assert _has_structural_depth(md) is False

    def test_returns_false_for_two_headings(self):
        """Needs >=3 headings for structural depth."""
        md = "# A\n\n## B"
        assert _has_structural_depth(md) is False
