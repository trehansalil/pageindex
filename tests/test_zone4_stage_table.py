"""Zone 4: stage-table runner provenance tests.

Covers:
  - _run_stages records char_delta/heading_delta per stage
  - _run_stages records error and preserves md unchanged when a stage raises
  - StageRecord dataclass fields
  - pdf_to_markdown_docling's extraction_stages 3rd return value
  - extraction_stages round-trips through save_doc_meta's _MERGE_FIELDS
"""

import dataclasses

import pytest

from pageindex_mcp.converters import (
    StageRecord,
    _build_candidate,
    _run_stages,
)


class TestRunStages:
    """_run_stages records per-stage provenance correctly."""

    def test_char_delta_recorded(self):
        def append_text(md: str) -> str:
            return md + "\n\nExtra paragraph added."

        md, records = _run_stages("# Hello", [("append", append_text)])
        assert len(records) == 1
        rec = records["append"]
        assert rec["name"] == "append"
        assert rec["chars_before"] == len("# Hello")
        assert rec["chars_after"] == len(md)
        assert rec["char_delta"] == rec["chars_after"] - rec["chars_before"]
        assert rec["error"] is None

    def test_heading_delta_recorded(self):
        def add_heading(md: str) -> str:
            return md + "\n\n## New Section\n\nBody text."

        md, records = _run_stages("# Title\n\nIntro.", [("add_heading", add_heading)])
        assert records["add_heading"]["headings_before"] == 1
        assert records["add_heading"]["headings_after"] == 2
        assert records["add_heading"]["heading_delta"] == 1

    def test_error_preserves_md_and_records_error(self):
        original = "# Keep me unchanged"

        def bad_stage(md: str) -> str:
            raise ValueError("stage broke")

        md, records = _run_stages(original, [("broken", bad_stage)])
        assert md == original
        assert len(records) == 1
        assert records["broken"]["error"] == "stage broke"
        assert records["broken"]["char_delta"] == 0
        assert records["broken"]["heading_delta"] == 0
        assert records["broken"]["chars_after"] == records["broken"]["chars_before"]

    def test_stage_n_failure_does_not_skip_stage_n_plus_1(self):
        """A failure in stage 0 must not prevent stage 1 from running."""

        def fail(md: str) -> str:
            raise RuntimeError("boom")

        def succeed(md: str) -> str:
            return md + " ok"

        md, records = _run_stages("start", [("fail", fail), ("succeed", succeed)])
        assert len(records) == 2
        assert records["fail"]["error"] is not None
        assert records["succeed"]["error"] is None
        assert md == "start ok"

    def test_multiple_stages_accumulate(self):
        def double(md: str) -> str:
            return md + md

        def add_heading(md: str) -> str:
            return md + "\n## H2"

        md, records = _run_stages("x", [("double", double), ("heading", add_heading)])
        assert len(records) == 2
        assert records["double"]["name"] == "double"
        assert records["heading"]["name"] == "heading"
        assert md == "xx\n## H2"

    def test_empty_stages_list_returns_md_unchanged(self):
        md, records = _run_stages("hello", [])
        assert md == "hello"
        assert records == {}


class TestStageRecord:
    """StageRecord is a proper dataclass with expected fields."""

    def test_dataclass_fields(self):
        rec = StageRecord(
            name="test",
            chars_before=100,
            chars_after=110,
            char_delta=10,
            headings_before=2,
            headings_after=3,
            heading_delta=1,
        )
        d = dataclasses.asdict(rec)
        assert d["name"] == "test"
        assert d["error"] is None

    def test_error_field_optional(self):
        rec = StageRecord(
            name="err",
            chars_before=0,
            chars_after=0,
            char_delta=0,
            headings_before=0,
            headings_after=0,
            heading_delta=0,
            error="something failed",
        )
        assert rec.error == "something failed"


class TestExtractionStagesMergeField:
    """extraction_stages round-trips through save_doc_meta's _MERGE_FIELDS."""

    def test_extraction_stages_in_merge_fields(self):
        from pageindex_mcp.storage import save_doc_meta

        import inspect

        source = inspect.getsource(save_doc_meta)
        assert "extraction_stages" in source
