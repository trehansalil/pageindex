"""Zone 6: Conversion Pipeline Stage Coupling — fallback pipeline decoupling +
candidate selection consolidation.

Covers:
  - Candidate.has_depth matches _has_structural_depth(candidate.md) for both
    flat-prose and multi-heading inputs
  - Regression: _heading_count is a thin wrapper consolidating
    len(_HEADING_RE.findall(md)) calls
  - _run_stages dict provenance unaffected by Zone 6 refactor
"""

import dataclasses

import pytest

from pageindex_mcp.converters import (
    Candidate,
    _candidate_from_document,
    _has_structural_depth,
    _heading_count,
    _run_stages,
)


# ---------------------------------------------------------------------------
# Contract: Candidate.has_depth matches _has_structural_depth
# ---------------------------------------------------------------------------


class TestCandidateHasDepth:
    """Candidate.has_depth caches _has_structural_depth(md) at construction
    time so the selection block reads it declaratively."""

    def test_flat_prose_has_depth_false(self):
        """Flat prose without structural headings -> has_depth = False."""
        flat_md = "Just some text without any headings at all."
        c = _candidate_from_document(flat_md, {}, "/fake.pdf")
        assert c.has_depth is False
        assert c.has_depth == _has_structural_depth(c.md)

    def test_single_heading_has_depth_false(self):
        """A single H1 heading is insufficient for structural depth."""
        md = "# Title\n\nBody text."
        c = _candidate_from_document(md, {}, "/fake.pdf")
        assert c.has_depth is False
        assert c.has_depth == _has_structural_depth(c.md)

    def test_two_headings_has_depth_false(self):
        """Two headings (below the >=3 threshold) -> has_depth = False."""
        md = "# Title\n\n## Section A\n\nBody."
        c = _candidate_from_document(md, {}, "/fake.pdf")
        assert c.has_depth is False
        assert c.has_depth == _has_structural_depth(c.md)

    def test_multi_heading_deep_tree_has_depth_true(self):
        """Three headings at depth>=2 -> has_depth = True."""
        md = "# Title\n\n## Section A\n\nBody A.\n\n## Section B\n\nBody B.\n\n## Section C\n\nBody C."
        c = _candidate_from_document(md, {}, "/fake.pdf")
        assert c.has_depth is True
        assert c.has_depth == _has_structural_depth(c.md)

    def test_has_depth_matches_standalone_function(self):
        """Candidate.has_depth must always agree with _has_structural_depth
        evaluated on the same .md text."""
        test_cases = [
            "No headings at all.",
            "# Only H1",
            "# H1\n\n## H2",
            "# H1\n\n## H2\n\n## H3\n\n## H4",
            "# A\n\n## B\n\n### C\n\nDeep content.\n\n## D\n\n## E",
        ]
        for md in test_cases:
            c = _candidate_from_document(md, {}, "/fake.pdf")
            expected = _has_structural_depth(c.md)
            assert c.has_depth == expected, (
                f"Mismatch for md={md!r}: Candidate.has_depth={c.has_depth}, "
                f"_has_structural_depth={expected}"
            )

    def test_has_depth_is_frozen(self):
        """has_depth cannot be mutated after construction (frozen dataclass)."""
        c = Candidate(md="# A\n\n## B\n\n## C\n\n## D", has_depth=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.has_depth = False  # type: ignore[misc]

    def test_has_depth_default_is_false(self):
        """Default value for has_depth is False (safe fallback for callers
        that construct Candidate with only md=)."""
        c = Candidate(md="# A\n\n## B\n\n## C\n\n## D")
        assert c.has_depth is False

    def test_candidate_from_document_computes_has_depth(self):
        """_candidate_from_document auto-computes has_depth, unlike manual
        Candidate() which defaults to False.

        The input must survive _recover_heading_depth without being flattened
        to all-H1 (empty heading_pages triggers containment relevel which can
        promote everything).  We construct input where _build_candidate +
        _recover_heading_depth preserve depth>=2 and count>=3."""
        # Use numbered sections so _relevel_by_numbering fires and keeps depth
        deep_md = (
            "# 1. Title\n\n"
            "## 1.1 Section A\n\nText.\n\n"
            "## 1.2 Section B\n\nText.\n\n"
            "## 1.3 Section C\n\nText."
        )
        c = _candidate_from_document(deep_md, {}, "/fake.pdf")
        # Verify has_depth agrees with _has_structural_depth on recovered md
        assert c.has_depth == _has_structural_depth(c.md)

    def test_candidate_preserves_heading_pages_identity(self):
        """Existing contract: heading_pages identity is preserved."""
        hp = {"# Title": [0, 1]}
        c = _candidate_from_document("# Title\n\nBody.", hp, "/fake.pdf")
        assert c.heading_pages is hp


# ---------------------------------------------------------------------------
# Contract: _heading_count consolidation
# ---------------------------------------------------------------------------


class TestHeadingCountHelper:
    """_heading_count is a thin wrapper consolidating repeated
    len(_HEADING_RE.findall(md)) patterns."""

    def test_zero_headings(self):
        assert _heading_count("Just plain text.") == 0

    def test_single_heading(self):
        assert _heading_count("# Title") == 1

    def test_multiple_headings(self):
        md = "# A\n\n## B\n\n### C\n\nText."
        assert _heading_count(md) == 3

    def test_headings_only_at_line_start(self):
        """Inline hash marks are not headings."""
        md = "Some text with # not a heading\n# Real heading"
        assert _heading_count(md) == 1

    def test_consistent_with_has_structural_depth(self):
        """_has_structural_depth uses _heading_count internally; verify
        the threshold relationship: depth requires count >= 3."""
        md_deep = "# A\n\n## B\n\n## C\n\n## D"
        assert _heading_count(md_deep) >= 3
        assert _has_structural_depth(md_deep) is True

        md_shallow = "# A\n\n## B"
        assert _heading_count(md_shallow) < 3
        assert _has_structural_depth(md_shallow) is False


# ---------------------------------------------------------------------------
# Regression: _run_stages dict provenance unaffected by Zone 6 refactor
# ---------------------------------------------------------------------------


class TestRunStagesRegressionZone6:
    """Verify _run_stages behavior is unchanged after Zone 6 refactoring
    (it now uses _heading_count helper internally)."""

    def test_char_delta_recorded(self):
        def add_suffix(md: str) -> str:
            return md + " extra"

        _, records = _run_stages("hello", [("add_suffix", add_suffix)])
        rec = records["add_suffix"]
        assert rec["chars_before"] == 5
        assert rec["chars_after"] == 11
        assert rec["char_delta"] == 6

    def test_heading_delta_recorded(self):
        def add_heading(md: str) -> str:
            return md + "\n\n## New Section"

        _, records = _run_stages("# Title", [("add_heading", add_heading)])
        rec = records["add_heading"]
        assert rec["headings_before"] == 1
        assert rec["headings_after"] == 2
        assert rec["heading_delta"] == 1

    def test_error_preserves_md_unchanged(self):
        def boom(md: str) -> str:
            raise RuntimeError("stage failure")

        result_md, records = _run_stages("original", [("boom", boom)])
        assert result_md == "original"
        assert records["boom"]["error"] == "stage failure"
        assert records["boom"]["char_delta"] == 0

    def test_stage_n_failure_does_not_skip_n_plus_1(self):
        def fail_stage(md: str) -> str:
            raise RuntimeError("fail")

        def ok_stage(md: str) -> str:
            return md + "!"

        result_md, records = _run_stages(
            "x", [("fail", fail_stage), ("ok", ok_stage)]
        )
        assert result_md == "x!"
        assert "fail" in records
        assert "ok" in records
        assert records["fail"]["error"] == "fail"
        assert records["ok"]["error"] is None

    def test_empty_stages_returns_md_unchanged(self):
        result_md, records = _run_stages("hello", [])
        assert result_md == "hello"
        assert records == {}
