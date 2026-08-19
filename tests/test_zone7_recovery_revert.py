"""Zone-7 recovery revert contract tests.

Validates that RecoveryOutcome.apply() restores all recovery-relevant fields
(tmp_md_path, route, rtl_decision, bidi_renorm_applied) and that _UNSET fields
leave ExtractionState unchanged.

Regression: RFC-029 D4 cabinet resolution keep-best revert (48k->14.8k chars)
fully restores pre-retry state including tmp_md_path pointing at a valid
on-disk tempfile.
"""

from __future__ import annotations

import dataclasses
import os
import tempfile

import pytest

from pageindex_mcp.helpers import (
    ExtractionState,
    RecoveryOutcome,
    Route,
    TreeDefect,
    TreeGateResult,
    _UNSET,
    _Unset,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(
    ok: bool = False,
    route: Route = Route.REJECT,
    first_defect: TreeDefect = TreeDefect.NODE_COUNT_LOW,
    gate_result: TreeGateResult | None = None,
    reason: str = "",
    bidi_renorm_applied: bool = False,
    tmp_md_path: str | None = None,
) -> ExtractionState:
    """Build a minimal ExtractionState for revert testing."""
    return ExtractionState(
        result={"structure": [{"node_id": "1", "title": "R", "text": "x" * 200, "nodes": []}]},
        ok=ok,
        reason=reason or first_defect.value,
        gate_result=gate_result,
        first_defect=first_defect,
        route=route,
        md_content="# test content",
        tmp_md_path=tmp_md_path,
        pic_results=[],
        used_converter="pymupdf4llm",
        total_chars=200,
        extraction_stages_captured=[],
        bidi_renorm_applied=bidi_renorm_applied,
    )


# ===========================================================================
# 1. apply() restores Zone-7 fields
# ===========================================================================


class TestApplyRestoresZone7Fields:
    """RecoveryOutcome.apply() restores tmp_md_path and bidi_renorm_applied."""

    def test_apply_restores_tmp_md_path(self):
        """apply() with tmp_md_path set writes it to state."""
        state = _make_state(tmp_md_path="/tmp/old.md")
        RecoveryOutcome(tmp_md_path="/tmp/pre_retry.md").apply(state)
        assert state.tmp_md_path == "/tmp/pre_retry.md"

    def test_apply_restores_tmp_md_path_none(self):
        """apply() with tmp_md_path=None clears it (distinct from _UNSET)."""
        state = _make_state(tmp_md_path="/tmp/old.md")
        RecoveryOutcome(tmp_md_path=None).apply(state)
        assert state.tmp_md_path is None

    def test_apply_unset_tmp_md_path_leaves_unchanged(self):
        """apply() with tmp_md_path=_UNSET leaves state.tmp_md_path alone."""
        state = _make_state(tmp_md_path="/tmp/original.md")
        RecoveryOutcome().apply(state)
        assert state.tmp_md_path == "/tmp/original.md"

    def test_apply_restores_bidi_renorm_applied_true(self):
        """apply() with bidi_renorm_applied=True writes it to state."""
        state = _make_state(bidi_renorm_applied=False)
        RecoveryOutcome(bidi_renorm_applied=True).apply(state)
        assert state.bidi_renorm_applied is True

    def test_apply_restores_bidi_renorm_applied_false(self):
        """apply() with bidi_renorm_applied=False writes it to state."""
        state = _make_state(bidi_renorm_applied=True)
        RecoveryOutcome(bidi_renorm_applied=False).apply(state)
        assert state.bidi_renorm_applied is False

    def test_apply_unset_bidi_renorm_leaves_unchanged(self):
        """apply() with bidi_renorm_applied=_UNSET leaves state unchanged."""
        state = _make_state(bidi_renorm_applied=True)
        RecoveryOutcome().apply(state)
        assert state.bidi_renorm_applied is True

    def test_apply_restores_route(self):
        """apply() with route set writes it to state."""
        state = _make_state(route=Route.REJECT)
        RecoveryOutcome(route=Route.TREE).apply(state)
        assert state.route == Route.TREE

    def test_apply_unset_route_leaves_unchanged(self):
        """apply() with route=_UNSET leaves state.route alone."""
        state = _make_state(route=Route.REJECT)
        RecoveryOutcome().apply(state)
        assert state.route == Route.REJECT

    def test_apply_restores_rtl_decision(self):
        """apply() with rtl_decision set writes it to state."""
        from pageindex_mcp.script import RtlDecision
        decision = RtlDecision(reversed=True, repair_effective=False, sampled=10, method="morphology")
        state = _make_state()
        RecoveryOutcome(rtl_decision=decision).apply(state)
        assert state.rtl_decision is decision

    def test_apply_restores_rtl_decision_none(self):
        """apply() with rtl_decision=None clears it."""
        from pageindex_mcp.script import RtlDecision
        state = _make_state()
        state.rtl_decision = RtlDecision(reversed=True, repair_effective=False, sampled=10, method="morphology")
        RecoveryOutcome(rtl_decision=None).apply(state)
        assert state.rtl_decision is None


# ===========================================================================
# 2. Full snapshot revert restores all fields consistently
# ===========================================================================


class TestFullSnapshotRevert:
    """A RecoveryOutcome capturing all fields restores them atomically."""

    def test_full_snapshot_revert(self):
        """All fields captured in pre_retry are restored by apply()."""
        from pageindex_mcp.script import RtlDecision

        pre_decision = RtlDecision(reversed=False, repair_effective=True, sampled=5, method="nfkc")
        gate = TreeGateResult(ok=True, defect=TreeDefect.OK)

        pre_retry = RecoveryOutcome(
            result={"structure": [{"node_id": "1", "title": "Pre", "text": "aaa", "nodes": []}]},
            ok=True,
            reason="ok",
            gate_result=gate,
            total_chars=48000,
            md_content="# pre-retry content",
            pic_results=[{"page": 1}],
            used_converter="docling",
            route=Route.TREE,
            rtl_decision=pre_decision,
            tmp_md_path="/tmp/pre_retry.md",
            bidi_renorm_applied=True,
        )

        # State is now post-retry (worse)
        state = _make_state(
            ok=False,
            route=Route.REJECT,
            bidi_renorm_applied=False,
            tmp_md_path="/tmp/post_retry.md",
        )
        state.total_chars = 14800
        state.md_content = "# post-retry content"
        state.used_converter = "pymupdf4llm"
        state.rtl_decision = None

        # Revert
        pre_retry.apply(state)

        assert state.ok is True
        assert state.reason == "ok"
        assert state.gate_result is gate
        assert state.total_chars == 48000
        assert state.md_content == "# pre-retry content"
        assert state.pic_results == [{"page": 1}]
        assert state.used_converter == "docling"
        assert state.route == Route.TREE
        assert state.rtl_decision is pre_decision
        assert state.tmp_md_path == "/tmp/pre_retry.md"
        assert state.bidi_renorm_applied is True

    def test_revert_does_not_touch_non_snapshot_fields(self):
        """Fields NOT in RecoveryOutcome (e.g. extraction_stages_captured,
        pre_garbled, pdf_page_count) are untouched by apply()."""
        state = _make_state()
        state.pre_garbled = True
        state.pdf_page_count = 42
        state.extraction_stages_captured = ["stage1"]

        RecoveryOutcome(ok=True, route=Route.TREE).apply(state)

        assert state.pre_garbled is True
        assert state.pdf_page_count == 42
        assert state.extraction_stages_captured == ["stage1"]


# ===========================================================================
# 3. Regression: RFC-029 D4 cabinet resolution keep-best revert
# ===========================================================================


class TestRfc029D4KeepBestRevert:
    """Regression test: keep-best revert after 48k->14.8k char regression
    fully restores pre-retry state including tmp_md_path pointing at a
    valid on-disk tempfile."""

    def test_revert_restores_tmp_md_path_and_content_roundtrip(self):
        """After apply() restores tmp_md_path, caller can re-materialise
        the tempfile from restored md_content."""
        pre_content = "# German insurance T&C\n" * 200  # ~4.6k chars
        post_content = "# Garbled OCR output"

        # Create a real pre-retry tempfile
        with tempfile.NamedTemporaryFile(
            suffix=".md", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write(pre_content)
            pre_path = f.name

        try:
            pre_retry = RecoveryOutcome(
                md_content=pre_content,
                tmp_md_path=pre_path,
                total_chars=len(pre_content),
                bidi_renorm_applied=False,
                route=Route.TREE,
            )

            # Simulate post-retry state (worse result)
            state = _make_state(
                route=Route.REJECT,
                bidi_renorm_applied=False,
                tmp_md_path="/tmp/post_retry_nonexistent.md",
            )
            state.md_content = post_content
            state.total_chars = len(post_content)

            # Revert via apply()
            pre_retry.apply(state)

            # Path string restored
            assert state.tmp_md_path == pre_path
            assert state.md_content == pre_content
            assert state.total_chars == len(pre_content)

            # The original tempfile still has valid content
            assert os.path.exists(pre_path)
            with open(pre_path, encoding="utf-8") as f:
                assert f.read() == pre_content
        finally:
            if os.path.exists(pre_path):
                os.unlink(pre_path)


# ===========================================================================
# 4. RecoveryOutcome Zone-7 fields exist in the dataclass
# ===========================================================================


class TestRecoveryOutcomeZone7Fields:
    """RecoveryOutcome must have tmp_md_path and bidi_renorm_applied fields."""

    def test_tmp_md_path_field_exists(self):
        field_names = {f.name for f in dataclasses.fields(RecoveryOutcome)}
        assert "tmp_md_path" in field_names

    def test_bidi_renorm_applied_field_exists(self):
        field_names = {f.name for f in dataclasses.fields(RecoveryOutcome)}
        assert "bidi_renorm_applied" in field_names

    def test_zone7_fields_default_to_unset(self):
        """Zone-7 fields default to _UNSET like all other fields."""
        ro = RecoveryOutcome()
        assert isinstance(ro.tmp_md_path, _Unset)
        assert isinstance(ro.bidi_renorm_applied, _Unset)

    def test_zone7_fields_accept_values(self):
        """Zone-7 fields can be set to concrete values."""
        ro = RecoveryOutcome(tmp_md_path="/tmp/test.md", bidi_renorm_applied=True)
        assert ro.tmp_md_path == "/tmp/test.md"
        assert ro.bidi_renorm_applied is True


# ===========================================================================
# 5. ExtractionState Zone-7 fields
# ===========================================================================


class TestExtractionStateZone7Fields:
    """ExtractionState must have bidi_renorm_applied with correct default."""

    def test_bidi_renorm_applied_field_exists(self):
        field_names = {f.name for f in dataclasses.fields(ExtractionState)}
        assert "bidi_renorm_applied" in field_names

    def test_bidi_renorm_applied_defaults_false(self):
        """bidi_renorm_applied defaults to False (no bidi renorm at construction)."""
        state = _make_state()
        assert state.bidi_renorm_applied is False

    def test_bidi_renorm_applied_settable(self):
        """bidi_renorm_applied can be set on a mutable ExtractionState."""
        state = _make_state()
        state.bidi_renorm_applied = True
        assert state.bidi_renorm_applied is True
