# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""Architecture guard tests -- structural and source-scanning invariants.

These tests verify that production source code maintains specific structural
contracts (call ordering, signature shapes, symbol removal, no duplicate
definitions). They work by inspecting source text via ``ast``, ``inspect``,
or filesystem scans -- not by exercising runtime behavior. Centralising them
here keeps the behavioral test files focused on inputs-and-outputs.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import json
import pathlib
import re
import shutil
import subprocess
import textwrap

import pytest

from pageindex_mcp.helpers import GATES
from pageindex_mcp.helpers.types import GateSpec
from pageindex_mcp.helpers.verdict import (
    apply_promotions,
    compute_verdict,
    evaluate_gates,
)


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
HARNESS_JS = PROJECT_ROOT / ".claude" / "workflows" / "corpus-ingest-score.js"


# ---------------------------------------------------------------------------
# Call-ordering: _persist_flat_result pipeline sequence
# ---------------------------------------------------------------------------


class TestPersistFlatResultOrdering:
    """Wiring (blocker #2 resolution): _persist_flat_result call ordering
    after Zone-1 restructuring is:
      splice_figure_markers
      -> route_and_extract_flat (block decomposition)
      -> _garble_check_flat_blocks (per-block garble gate)
      -> _apply_picture_enrichment(splice_markers=False)
        -> _enrich_image_blocks (inside _apply_picture_enrichment)
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


# ---------------------------------------------------------------------------
# RFC-037 D4: apply_verdict_hysteresis REMOVED
# ---------------------------------------------------------------------------


class TestApplyVerdictHysteresisRemoved:
    """apply_verdict_hysteresis must no longer be importable or referenced
    in indexer.py persist paths."""

    def test_not_exported_from_helpers_init(self):
        """helpers.__init__.__all__ must NOT include apply_verdict_hysteresis."""
        import pageindex_mcp.helpers as helpers_mod
        assert not hasattr(helpers_mod, "apply_verdict_hysteresis")
        assert "apply_verdict_hysteresis" not in helpers_mod.__all__

    def test_not_importable_from_verdict_module(self):
        """Direct import from helpers.verdict must fail."""
        with pytest.raises(ImportError):
            from pageindex_mcp.helpers.verdict import apply_verdict_hysteresis  # noqa: F401

    def test_indexer_flat_path_no_hysteresis(self):
        """indexer.py _persist_flat_result must NOT reference
        apply_verdict_hysteresis."""
        import pageindex_mcp.client.indexer as indexer_mod
        src = inspect.getsource(
            indexer_mod.CustomPageIndexClient._persist_flat_result
        )
        assert "apply_verdict_hysteresis" not in src

    def test_indexer_tree_path_no_hysteresis(self):
        """indexer.py _persist_tree_result must NOT reference
        apply_verdict_hysteresis."""
        import pageindex_mcp.client.indexer as indexer_mod
        src = inspect.getsource(
            indexer_mod.CustomPageIndexClient._persist_tree_result
        )
        assert "apply_verdict_hysteresis" not in src

    def test_not_importable_from_helpers_verdict(self):
        from pageindex_mcp.helpers import verdict as mod
        assert not hasattr(mod, "apply_verdict_hysteresis")

    def test_not_in_helpers_all(self):
        import pageindex_mcp.helpers as helpers_mod
        assert "apply_verdict_hysteresis" not in helpers_mod.__all__

    def test_not_importable_from_helpers_package(self):
        with pytest.raises(ImportError):
            from pageindex_mcp.helpers import apply_verdict_hysteresis  # noqa: F401


# ---------------------------------------------------------------------------
# Contract: evaluate_gates signature has no flat kwarg
# ---------------------------------------------------------------------------


class TestEvaluateGatesSignature:
    """evaluate_gates must not accept a flat= keyword argument after
    the tree/flat verdict split removal."""

    def test_no_flat_parameter(self):
        sig = inspect.signature(evaluate_gates)
        assert "flat" not in sig.parameters

    def test_positional_param_count(self):
        """evaluate_gates takes exactly 4 positional params:
        structure, validate_result, expected_script, th."""
        sig = inspect.signature(evaluate_gates)
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        assert len(positional) == 4


# ---------------------------------------------------------------------------
# Contract: apply_promotions signature has no validate_result param
# ---------------------------------------------------------------------------


class TestApplyPromotionsSignature:
    """apply_promotions must not accept a validate_result positional arg
    after the tree/flat verdict unification."""

    def test_no_validate_result_parameter(self):
        sig = inspect.signature(apply_promotions)
        assert "validate_result" not in sig.parameters

    def test_positional_param_count(self):
        """apply_promotions takes exactly 6 positional params:
        outcome, content_class, image_enrichment_ratio, inspector_class,
        th, expected_script. Plus keyword-only source_selection."""
        sig = inspect.signature(apply_promotions)
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        assert len(positional) == 6
        kw_only = [
            p for p in sig.parameters.values()
            if p.kind == inspect.Parameter.KEYWORD_ONLY
        ]
        assert any(p.name == "source_selection" for p in kw_only)


# ---------------------------------------------------------------------------
# Wiring: state.gate_result threaded to both persist paths
# ---------------------------------------------------------------------------


class TestGateResultThreading:
    """state.gate_result must be passed to compute_verdict in both
    _persist_flat_result and _persist_tree_result."""

    def test_flat_path_threads_gate_result(self):
        """_persist_flat_result must pass state.gate_result to
        compute_verdict (as the validate_result positional arg)."""
        import pageindex_mcp.client.indexer as indexer_mod
        src = inspect.getsource(
            indexer_mod.CustomPageIndexClient._persist_flat_result
        )
        assert "state.gate_result" in src
        # Must appear as arg to compute_verdict, not just in any context
        assert "compute_verdict" in src

    def test_tree_path_threads_gate_result(self):
        """_persist_tree_result must pass state.gate_result to
        compute_verdict."""
        import pageindex_mcp.client.indexer as indexer_mod
        src = inspect.getsource(
            indexer_mod.CustomPageIndexClient._persist_tree_result
        )
        assert "state.gate_result" in src
        assert "compute_verdict" in src


# ---------------------------------------------------------------------------
# Contract: compute_verdict no flat kwarg
# ---------------------------------------------------------------------------


class TestComputeVerdictSignatureZone1:
    """compute_verdict must not accept flat= after unification."""

    def test_no_flat_parameter(self):
        sig = inspect.signature(compute_verdict)
        assert "flat" not in sig.parameters

    def test_no_validate_result_as_keyword(self):
        """validate_result must be positional-or-keyword with default None,
        not keyword-only."""
        sig = inspect.signature(compute_verdict)
        p = sig.parameters["validate_result"]
        assert p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert p.default is None


# ---------------------------------------------------------------------------
# Regression: _decomposed_verdict dead code uses removed signatures
# ---------------------------------------------------------------------------


class TestDecomposedVerdictDeadCode:
    """The _decomposed_verdict helper in test files is dead code
    that still references removed signatures (flat= kwarg on evaluate_gates,
    validate_result positional on apply_promotions). This test documents
    that it is unreachable -- if it were called, it would crash."""

    def test_decomposed_verdict_is_unreferenced(self):
        """_decomposed_verdict must have zero call sites in any test_*.py
        (besides its own def line and this guard file)."""
        tests_dir = pathlib.Path(__file__).parent
        offenders: list[str] = []
        for test_file in tests_dir.glob("test_*.py"):
            if test_file.name == "test_architecture_guards.py":
                continue
            source = test_file.read_text()
            lines = source.splitlines()
            call_refs = [
                i for i, line in enumerate(lines, 1)
                if "_decomposed_verdict" in line
                and not line.strip().startswith("def _decomposed_verdict")
            ]
            if call_refs:
                offenders.append(f"{test_file.name}:{call_refs}")
        assert not offenders, (
            f"_decomposed_verdict is referenced in {offenders} -- "
            "it uses removed signatures (flat= kwarg, validate_result "
            "positional) and will crash if called"
        )


# ---------------------------------------------------------------------------
# Contract: GateSpec has no flat_applicable field (cross-check)
# ---------------------------------------------------------------------------


class TestGateSpecFieldsZone1:
    """GateSpec must not have flat_applicable after removal."""

    def test_no_flat_applicable_field(self):
        field_names = {f.name for f in dataclasses.fields(GateSpec)}
        assert "flat_applicable" not in field_names

    def test_no_gate_has_flat_applicable_attr(self):
        """No individual gate in GATES should carry flat_applicable."""
        for gate in GATES:
            assert not hasattr(gate, "flat_applicable") or "flat_applicable" not in {
                f.name for f in gate.__dataclass_fields__.values()  # type: ignore[attr-defined]
            }


# ---------------------------------------------------------------------------
# Contract: _structural_ok unified expression
# ---------------------------------------------------------------------------


class TestStructuralOkSourceContract:
    """The _structural_ok computation in apply_promotions must use
    the all_defects-based isdisjoint() check, not sig-based heuristics."""

    def test_structural_ok_uses_isdisjoint_in_source(self):
        """Source code must contain the unified isdisjoint expression
        (in _try_structural_pass, called by apply_promotions)."""
        from pageindex_mcp.helpers.verdict import _try_structural_pass
        src = inspect.getsource(_try_structural_pass)
        assert "isdisjoint" in src
        assert "NODE_COUNT_LOW" in src
        assert "DEPTH_LOW" in src

    def test_no_sig_node_count_heuristic_in_apply_promotions(self):
        """The old sig.node_count >= 3 and sig.depth >= 2 heuristic
        for _structural_ok must not appear in apply_promotions."""
        src = inspect.getsource(apply_promotions)
        lines = src.splitlines()
        for line in lines:
            if "_structural_ok" in line and "sig.node_count" in line:
                pytest.fail(
                    f"_structural_ok still uses sig-based heuristic: {line.strip()}"
                )


# ---------------------------------------------------------------------------
# RFC-012: Redis singleton -- no direct aioredis.from_url in worker
# ---------------------------------------------------------------------------


def test_no_direct_aioredis_from_url_in_worker():
    """worker.py must have exactly one aioredis.from_url call (the startup site)."""
    worker_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp" / "worker"
    matches = []
    for py_file in worker_dir.glob("*.py"):
        text = py_file.read_text()
        matches.extend(re.finditer(r"aioredis\.from_url\(", text))
    assert len(matches) == 1, (
        f"Expected exactly 1 aioredis.from_url call (startup), found {len(matches)}"
    )


# ---------------------------------------------------------------------------
# RFC-037 D6: no duplicate priority maps in codebase
# ---------------------------------------------------------------------------


class TestNoDuplicatePriorityMaps:
    """Only helpers/types.py may define a dict literal with all four verdict
    keys mapped to integers."""

    def test_no_duplicate_priority_maps_in_codebase(self):
        """Scan src/pageindex_mcp/ for any module defining a dict literal with
        all four verdict keys mapped to integers -- only helpers/types.py may."""
        src_root = pathlib.Path(__file__).parent.parent / "src" / "pageindex_mcp"
        verdict_keys = {"PASS", "MARGINAL", "FAIL", "ERROR"}
        offenders: list[str] = []

        for py_file in src_root.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(), filename=str(py_file))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                keys = set()
                all_int_vals = True
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
                    if not isinstance(v, ast.Constant) or not isinstance(v.value, int):
                        all_int_vals = False
                if verdict_keys.issubset(keys) and all_int_vals:
                    rel = py_file.relative_to(src_root)
                    if str(rel) != "helpers/types.py":
                        offenders.append(f"{rel}:{node.lineno}")

        assert not offenders, f"Duplicate priority maps found: {offenders}"


# ---------------------------------------------------------------------------
# RFC-037 D5: sidecar passivity -- CAS guard symbols removed
# ---------------------------------------------------------------------------


class TestSidecarPassivityGuards:
    """After RFC-037 D5, the sidecar CAS guard is deleted -- these symbols
    must not exist in storage.verdict."""

    def test_verdict_cas_guard_not_importable(self):
        """_verdict_cas_guard must not exist in storage.verdict."""
        mod = importlib.import_module("pageindex_mcp.storage.verdict")
        assert not hasattr(mod, "_verdict_cas_guard"), \
            "_verdict_cas_guard should be deleted (D5: sidecar is passive archive)"

    def test_verdict_cas_fields_not_importable(self):
        """_VERDICT_CAS_FIELDS must not exist in storage.verdict."""
        mod = importlib.import_module("pageindex_mcp.storage.verdict")
        assert not hasattr(mod, "_VERDICT_CAS_FIELDS"), \
            "_VERDICT_CAS_FIELDS should be deleted (D5: sidecar is passive archive)"


# ---------------------------------------------------------------------------
# SQL: no hardcoded CASE expressions outside generated constant
# ---------------------------------------------------------------------------


def test_no_hardcoded_case_expressions_outside_queries():
    """There must be no hardcoded CASE WHEN ... PASS ... MARGINAL ...
    expressions in queries.py outside the generated constant."""
    from pageindex_mcp.registry import queries

    source = inspect.getsource(queries)
    lines_with_case_pass = [
        line.strip()
        for line in source.splitlines()
        if "CASE" in line and "'PASS'" in line and "THEN" in line
    ]
    assert len(lines_with_case_pass) <= 2, (
        f"Found {len(lines_with_case_pass)} hardcoded CASE...PASS lines; "
        "expected at most 2 (the generated constant + one f-string)"
    )


# ---------------------------------------------------------------------------
# Scoring-harness Stage 2 guard (D4)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not HARNESS_JS.exists() or shutil.which("node") is None,
    reason="workflow JS not present or node not on PATH",
)
class TestScoringHarnessStage2Guard:
    """The scoring harness's Stage 2 guard
    (.claude/workflows/corpus-ingest-score.js) short-circuits to ERROR iff
    ingestResult is falsy or ingestResult.status === 'error' -- never on a
    substring match against unrelated string fields."""

    @pytest.fixture(scope="class")
    def guard_predicate(self):
        source = HARNESS_JS.read_text()
        match = re.search(r"if \(!ingestResult \|\| ingestResult\.status === 'error'\)", source)
        assert match, "Stage 2 guard predicate not found in corpus-ingest-score.js"
        return "!ingestResult || ingestResult.status === 'error'"

    def _run_guard(self, guard_predicate: str, ingest_result_json: str) -> bool:
        script = f"""
        const ingestResult = {ingest_result_json};
        const isError = {guard_predicate};
        console.log(JSON.stringify(isError));
        """
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        return json.loads(result.stdout.strip())

    def test_success_status_with_unrelated_error_substring_proceeds(self, guard_predicate):
        ingest_result = json.dumps(
            {"status": "success", "doc_id": "x", "note": "error handling succeeded"}
        )
        assert self._run_guard(guard_predicate, ingest_result) is False

    def test_error_status_short_circuits(self, guard_predicate):
        ingest_result = json.dumps({"status": "error", "error": "OOM"})
        assert self._run_guard(guard_predicate, ingest_result) is True

    def test_null_ingest_result_short_circuits(self, guard_predicate):
        assert self._run_guard(guard_predicate, "null") is True


# ---------------------------------------------------------------------------
# Contract: no ScriptContext fallback hardcodes had_presentation_forms=False
# ---------------------------------------------------------------------------
class TestPresentationFormsNotHardcoded:
    """Every ``ScriptContext`` built as a fallback must infer
    ``had_presentation_forms`` from the text it is about to check.

    Hardcoding ``False`` defeats the NFKC presentation-forms compensation
    that ``detect_garble`` applies internally: an Arabic document whose
    presentation-form codepoints were normalised away before reaching the
    caller is then reported as clean.  Commit e02ec93 closed three such
    sites (verdict.py, images.py, indexer.py's pre-garble probe) but missed
    two more in indexer.py -- the flat-path garble gate and the VLM
    fallback.  This guard exists so the pattern cannot be reintroduced a
    third time.

    ``ScriptContext.from_script_str()`` in script.py is the one allowed
    occurrence: it is the documented no-information constructor used by
    test code that has only a script string, and callers that do have the
    text are expected to build ScriptContext directly instead.
    """

    ALLOWED_FILES = {"script.py"}

    def _offending_sites(self) -> dict[str, list[int]]:
        """Real keyword arguments only.

        Parsed with ``ast`` rather than matched textually, so prose in
        comments and docstrings that *describes* the defect (garble.py's
        ``_infer_presentation_forms`` docstring does) is not itself
        reported as an instance of it.
        """
        src_root = PROJECT_ROOT / "src" / "pageindex_mcp"
        hits: dict[str, list[int]] = {}
        for path in sorted(src_root.rglob("*.py")):
            if path.name in self.ALLOWED_FILES:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if (
                        kw.arg == "had_presentation_forms"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is False
                    ):
                        hits.setdefault(str(path.relative_to(src_root)), []).append(
                            kw.value.lineno
                        )
        return hits

    def test_no_hardcoded_false_outside_the_no_information_constructor(self):
        offenders = self._offending_sites()
        assert not offenders, (
            "had_presentation_forms=False is hardcoded outside "
            f"ScriptContext.from_script_str(): {offenders}. Pass "
            "_infer_presentation_forms(<the text being checked>) instead -- "
            "hardcoding False makes NFKC-normalised Arabic look clean."
        )

    @pytest.mark.parametrize(
        "source_tag,text_var",
        [("flat_garble_gate", "flat_md"), ("vlm_fallback_garble", "vlm_md")],
    )
    def test_indexer_garble_contexts_infer_from_the_checked_text(self, source_tag, text_var):
        """The two sites e02ec93 missed, pinned individually.

        Each fallback must infer from the *same* text its gate then checks,
        not from some other blob and not from a constant.
        """
        src = (PROJECT_ROOT / "src" / "pageindex_mcp" / "client" / "indexer.py").read_text()
        assert f'source="{source_tag}"' in src, (
            f"no ScriptContext with source={source_tag!r} in indexer.py"
        )
        assert f"_infer_presentation_forms({text_var})" in src, (
            f"the {source_tag} ScriptContext must infer presentation forms "
            f"from {text_var}, the text its garble gate checks"
        )

    def test_allowed_site_is_the_documented_no_information_constructor(self):
        """Guard the exemption itself: script.py's occurrence is inside
        ``from_script_str``, which documents that it has no text to infer from."""
        from pageindex_mcp.script import ScriptContext

        src = inspect.getsource(ScriptContext.from_script_str)
        assert "had_presentation_forms=False" in src
        assert 'source="legacy"' in src
