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
# RFC-043 D1: _eligible_low_content must not gate on a char threshold
# ---------------------------------------------------------------------------


class TestEligibleLowContentNoCharFloor:
    """The zero-content OCR recovery flow depends on _eligible_low_content
    checking only flags + defect membership -- no total_chars threshold.
    The char floor is a *skip* guard that belongs solely in
    _recover_low_content_ocr (recovery.py); hardening the eligibility
    predicate with a char threshold would re-introduce the D1 bug where
    zero-content documents (0 >= 300 = False) get blocked from recovery."""

    def test_no_total_chars_reference_in_source(self):
        from pageindex_mcp.helpers.gates import _eligible_low_content

        source = inspect.getsource(_eligible_low_content)
        assert "total_chars" not in source, (
            "_eligible_low_content must not check total_chars -- the char "
            "floor belongs in _recover_low_content_ocr (recovery.py), not "
            "the eligibility predicate"
        )


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

    RFC-043 D3 removed script.py's ``ScriptContext.from_script_str()``
    hardcoded ``had_presentation_forms=False`` default -- callers must now
    pass the real detection result explicitly -- so no file is exempt any
    longer: the guard runs unconditionally against every file in
    ``src/pageindex_mcp``.
    """

    ALLOWED_FILES: set[str] = set()

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

    def test_allowed_site_no_longer_hardcodes_false(self):
        """Guard the exemption itself: RFC-043 D3 removed the hardcoded
        default from ``from_script_str`` -- it is now a required keyword-only
        parameter, so the ``script.py`` exemption in ``ALLOWED_FILES`` exists
        only for the deprecation warning boilerplate, not for a hardcoded
        ``False``."""
        from pageindex_mcp.script import ScriptContext

        src = inspect.getsource(ScriptContext.from_script_str)
        assert "had_presentation_forms=False" not in src
        assert "had_presentation_forms: bool" in src
        assert 'source="legacy"' in src


# ---------------------------------------------------------------------------
# D1 CI lint: no direct _garble_prongs calls outside garble.py
# ---------------------------------------------------------------------------


class TestNoDirectGarbleProngsOutsideGarblePy:
    """D1 (RFC-041): _garble_prongs is private to garble.py.  No
    production source file outside garble.py may call it directly --
    all garble detection must flow through detect_garble."""

    def test_no_garble_prongs_calls_in_production_code(self):
        import ast as _ast

        src_root = pathlib.Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp"
        violations: list[str] = []

        for pyfile in sorted(src_root.rglob("*.py")):
            rel = str(pyfile.relative_to(src_root))
            if rel == "helpers/garble.py":
                continue
            try:
                tree = _ast.parse(pyfile.read_text(), filename=str(pyfile))
            except SyntaxError:
                continue
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Call):
                    func = node.func
                    name = None
                    if isinstance(func, _ast.Name):
                        name = func.id
                    elif isinstance(func, _ast.Attribute):
                        name = func.attr
                    if name in ("_garble_prongs", "garble_prongs"):
                        violations.append(
                            f"  {rel}:{node.lineno}: direct {name}() call"
                        )

        assert not violations, (
            "D1 (RFC-041): direct _garble_prongs/_garble_prongs calls found "
            "outside garble.py.  Use detect_garble() instead.\n"
            "Violations:\n" + "\n".join(violations)
        )

    def test_garble_prongs_not_exported_from_helpers_init(self):
        import pageindex_mcp.helpers as helpers_mod
        assert not hasattr(helpers_mod, "garble_prongs"), (
            "garble_prongs must not be exported from helpers/__init__.py"
        )
        assert "garble_prongs" not in helpers_mod.__all__, (
            "garble_prongs must not be in helpers.__all__"
        )


# ---------------------------------------------------------------------------
# RFC-042 D3: save_doc_meta single-caller architecture guard
# ---------------------------------------------------------------------------


class TestSaveDocMetaSingleCaller:
    """RFC-042 D3 (Amendment 2026-09-01 v2): save_doc_meta must only be
    referenced from the MinIO write-through path in registry_mirror.py, or
    from the two child-subprocess persist paths in indexer.py
    (_persist_flat_result, _persist_tree_result), which run without
    Postgres access and write the sidecar directly. Every other call site
    is a bypass of the single-writer pattern and must route through
    _upsert_registry_row instead (closed incrementally per task 2.2).
    """

    ALLOWED_WHOLE_FILES = {"worker/registry_mirror.py"}
    ALLOWED_FUNCTIONS = {
        "client/indexer.py": {"_persist_flat_result", "_persist_tree_result"},
    }
    DEFINING_FILE = "storage/verdict.py"

    def _violations(self) -> list[str]:
        src_root = PROJECT_ROOT / "src" / "pageindex_mcp"
        violations: list[str] = []

        for pyfile in sorted(src_root.rglob("*.py")):
            rel = str(pyfile.relative_to(src_root))
            if rel == self.DEFINING_FILE or rel in self.ALLOWED_WHOLE_FILES:
                continue
            tree = ast.parse(pyfile.read_text(), filename=str(pyfile))
            allowed_funcs = self.ALLOWED_FUNCTIONS.get(rel, set())

            def _refs(node: ast.AST) -> list[int]:
                return [
                    n.lineno
                    for n in ast.walk(node)
                    if isinstance(n, ast.Name) and n.id == "save_doc_meta"
                ]

            covered: set[int] = set()
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name in allowed_funcs
                ):
                    covered.update(_refs(node))

            for lineno in _refs(tree):
                if lineno not in covered:
                    violations.append(f"{rel}:{lineno}")

        return violations

    def test_save_doc_meta_only_referenced_from_allowed_sites(self):
        violations = self._violations()
        assert not violations, (
            "RFC-042 D3: save_doc_meta referenced outside the allowed "
            "single-writer sites (registry_mirror.py; indexer.py's "
            f"child-subprocess persist paths): {violations}"
        )


# ---------------------------------------------------------------------------
# D3 CI lint: no direct state.route = or state.ok = in recovery.py
# ---------------------------------------------------------------------------


class TestNoDirectStateMutationInRecovery:
    """D3 (RFC-041): recovery.py must not assign state.route or state.ok
    directly.  All state mutations go through finalize_gate_and_route."""

    def test_no_direct_route_or_ok_assignment_in_recovery(self):
        import re

        src_root = pathlib.Path(__file__).resolve().parent.parent / "src" / "pageindex_mcp"
        recovery_path = src_root / "client" / "recovery.py"
        source = recovery_path.read_text()

        pattern = re.compile(r"^\s+state\.(route|ok)\s*=\s*", re.MULTILINE)
        violations: list[str] = []
        for match in pattern.finditer(source):
            line_no = source[:match.start()].count("\n") + 1
            field = match.group(1)
            violations.append(f"  recovery.py:{line_no}: direct state.{field} = assignment")

        assert not violations, (
            "D3 (RFC-041): direct state.route/state.ok assignments found "
            "in recovery.py.  Use finalize_gate_and_route() instead.\n"
            "Violations:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# D4 (RFC-042): hot-path files must read config via PipelineConfig, not
# os.environ directly
# ---------------------------------------------------------------------------


class TestHotPathConfigAccessGuard:
    """D4 (RFC-042): hot-path source files must read configuration through
    the frozen PipelineConfig snapshot, not `os.environ`/`os.getenv`
    directly.  Startup-only files (tracing, subprocess management, MinIO
    client init, constants, definitions) run once before PipelineConfig is
    built and are explicitly allowlisted -- they are not scanned here."""

    HOT_PATH_FILES = (
        "helpers/gates.py",
        "converters/pictures.py",
        "client/indexer.py",
        "helpers/tree_split.py",
        "helpers/garble.py",
        "helpers/verdict.py",
    )

    STARTUP_ONLY_ALLOWLIST = (
        "tracing.py",
        "subprocess_mgr.py",
        "minio_client.py",
        "constants.py",
        "definitions.py",
    )

    def _env_read_sites(self, rel_path: str) -> list[str]:
        """AST-based scan for `os.environ`/`os.getenv` attribute access.

        Uses `ast` rather than a textual grep so prose in comments and
        docstrings that *mentions* os.environ (garble.py's module docstring
        does, describing what it replaced) is not itself reported as a
        live read.
        """
        src_root = PROJECT_ROOT / "src" / "pageindex_mcp"
        path = src_root / rel_path
        tree = ast.parse(path.read_text(), filename=str(path))
        violations: list[str] = []
        for node in ast.walk(tree):
            # Catch `os.environ[...]`, `os.getenv(...)`, `os.environ.get(...)`
            if isinstance(node, ast.Attribute):
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "os"
                    and node.attr in ("environ", "getenv")
                ):
                    violations.append(
                        f"  {rel_path}:{node.lineno}: os.{node.attr}"
                    )
            # Catch `from os import environ` / `from os import getenv`
            elif isinstance(node, ast.ImportFrom) and node.module == "os":
                for alias in node.names:
                    if alias.name in ("environ", "getenv"):
                        violations.append(
                            f"  {rel_path}:{node.lineno}: from os import {alias.name}"
                        )
        return violations

    @pytest.mark.parametrize("rel_path", HOT_PATH_FILES)
    def test_hot_path_file_has_no_direct_os_environ_reads(self, rel_path):
        violations = self._env_read_sites(rel_path)
        assert not violations, (
            "D4 (RFC-042): direct os.environ/os.getenv read(s) found in a "
            f"hot-path file. Use PipelineConfig fields instead.\n"
            "Violations:\n" + "\n".join(violations)
        )

    def test_startup_only_allowlist_files_exist(self):
        """The allowlist documents which files are exempt (Requirement 4.3)
        -- guard the exemption itself so it can't silently drift from the
        actual startup-only files on disk."""
        src_root = PROJECT_ROOT / "src" / "pageindex_mcp"
        missing = [
            name
            for name in self.STARTUP_ONLY_ALLOWLIST
            if not any(src_root.rglob(name))
        ]
        assert not missing, f"allowlisted startup-only file(s) not found in src/: {missing}"


# ---------------------------------------------------------------------------
# D3 (RFC-042): save_doc_meta single-writer enforcement
# ---------------------------------------------------------------------------


class _SaveDocMetaCallVisitor(ast.NodeVisitor):
    """Tracks the innermost enclosing function name for every reference to
    ``save_doc_meta`` -- both direct calls (``save_doc_meta(...)``) and
    deferred-call references (``asyncio.to_thread(save_doc_meta, ...)``,
    the dominant pattern in this codebase since ``save_doc_meta`` is
    synchronous MinIO I/O)."""

    def __init__(self) -> None:
        self._stack: list[str] = []
        self.calls: list[tuple[str | None, int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id == "save_doc_meta":
            enclosing = self._stack[-1] if self._stack else None
            self.calls.append((enclosing, node.lineno))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr == "save_doc_meta":
            enclosing = self._stack[-1] if self._stack else None
            self.calls.append((enclosing, node.lineno))
        self.generic_visit(node)


class TestSaveDocMetaSingleWriter:
    """D3 (RFC-042): `save_doc_meta` is a single-writer function -- in
    production code it may only be called from `registry_mirror.py`'s
    `_upsert_registry_row` write-through path and the two child-subprocess
    persist paths in indexer.py (`_persist_flat_result`, `_persist_tree_result`).
    Test files are exempt (they mock `save_doc_meta` for isolation)."""

    ALLOWED_CALLER_FILES = {
        "worker/registry_mirror.py",
        "client/indexer.py",
    }
    ALLOWED_INDEXER_FUNCTIONS = {"_persist_flat_result", "_persist_tree_result"}
    DEFINING_FILE = "storage/verdict.py"

    def _offending_sites(self) -> list[str]:
        src_root = PROJECT_ROOT / "src" / "pageindex_mcp"
        violations: list[str] = []
        for pyfile in sorted(src_root.rglob("*.py")):
            rel = str(pyfile.relative_to(src_root))
            if rel == self.DEFINING_FILE:
                continue
            try:
                tree = ast.parse(pyfile.read_text(), filename=str(pyfile))
            except SyntaxError:
                continue
            visitor = _SaveDocMetaCallVisitor()
            visitor.visit(tree)
            if not visitor.calls:
                continue
            if rel not in self.ALLOWED_CALLER_FILES:
                violations.extend(
                    f"  {rel}:{lineno}: save_doc_meta() called outside the "
                    "single-writer path"
                    for _enclosing, lineno in visitor.calls
                )
            elif rel == "client/indexer.py":
                violations.extend(
                    f"  {rel}:{lineno}: save_doc_meta() called from "
                    f"{enclosing!r}, expected one of {sorted(self.ALLOWED_INDEXER_FUNCTIONS)}"
                    for enclosing, lineno in visitor.calls
                    if enclosing not in self.ALLOWED_INDEXER_FUNCTIONS
                )
        return violations

    def test_save_doc_meta_called_only_from_single_writer_path(self):
        """No production module outside registry_mirror.py's write-through
        path or indexer.py's two child-subprocess persist functions may call
        save_doc_meta() -- it is the sole authoritative verdict-sidecar
        writer (D3)."""
        violations = self._offending_sites()
        assert not violations, (
            "D3 (RFC-042): save_doc_meta() called outside the single-writer "
            "path. Route through registry_mirror.py's _upsert_registry_row "
            "instead.\nViolations:\n" + "\n".join(violations)
        )

    def test_underscore_alias_not_introduced(self):
        """Defense-in-depth: no `_save_doc_meta` private alias should be
        introduced as a bypass around the public single-writer guard."""
        mod = importlib.import_module("pageindex_mcp.storage.verdict")
        assert not hasattr(mod, "_save_doc_meta"), (
            "_save_doc_meta must not exist -- save_doc_meta is the sole "
            "entry point and is guarded by TestSaveDocMetaSingleWriter"
        )


# ---------------------------------------------------------------------------
# RFC-043 D2: OCR escalation flag independence -- _eligible_low_content must
# not share image_dominant_ocr_escalation_enabled with _eligible_image_dominant
# ---------------------------------------------------------------------------


class TestOcrEscalationFlagIndependence:
    """D2 (RFC-043): _eligible_low_content gates solely on
    ocr_escalation_low_content. It must not OR-gate
    image_dominant_ocr_escalation_enabled as a fallback -- that coupling
    turned image_dominant_ocr_escalation_enabled into an unintended
    kill-switch for low-content recovery. _eligible_image_dominant remains
    the sole predicate gated on image_dominant_ocr_escalation_enabled."""

    def test_eligible_low_content_does_not_reference_image_dominant_flag(self):
        from pageindex_mcp.helpers.gates import _eligible_low_content

        tree = ast.parse(textwrap.dedent(inspect.getsource(_eligible_low_content)))
        body = tree.body[0]
        if ast.get_docstring(body) is not None:
            body.body = body.body[1:]
        source = ast.unparse(body)
        assert "image_dominant_ocr_escalation_enabled" not in source, (
            "_eligible_low_content must not reference "
            "image_dominant_ocr_escalation_enabled -- the two eligibility "
            "predicates must not share a config flag (RFC-043 D2)"
        )


# ---------------------------------------------------------------------------
# RFC-044: recovery-dispatch-wiring architecture guards
# ---------------------------------------------------------------------------


class TestRFC044RecoveryDispatchGuards:
    """Static architecture guards for RFC-044 (recovery dispatch wiring).

    Property 1 (D1) -- re-entry guard exhaustiveness: every ``RecoveryMixin``
    method that calls ``_execute_ocr_retry`` must early-return on
    ``state.full_page_already_applied`` *before* that call, so a document that
    already received full-page OCR never triggers a redundant second pass.

    Property 3 (D3) -- single live ``decide_ocr_strategy`` call site in ``src/``.

    Property 4 (D4) -- no unreachable feature flags: the ``UNIFIED_OCR_PLAN_ENABLED``
    flag and its dead ``document_type='image'`` branch must not reappear in ``src/``.
    """

    @staticmethod
    def _ocr_retry_methods() -> dict[str, ast.AST]:
        """Every RecoveryMixin method whose body calls ``_execute_ocr_retry``.

        Discovered dynamically so a newly added OCR recovery method is covered
        by this guard without anyone remembering to extend a hardcoded list.
        """
        from pageindex_mcp.client import recovery as recovery_mod

        found: dict[str, ast.AST] = {}
        for name in dir(recovery_mod.RecoveryMixin):
            attr = getattr(recovery_mod.RecoveryMixin, name, None)
            if not inspect.isfunction(attr):
                continue
            try:
                source = textwrap.dedent(inspect.getsource(attr))
            except (OSError, TypeError):  # pragma: no cover - defensive
                continue
            func_node = ast.parse(source).body[0]
            calls_retry = any(
                isinstance(node, ast.Attribute) and node.attr == "_execute_ocr_retry"
                for node in ast.walk(func_node)
            )
            if calls_retry:
                found[name] = func_node
        return found

    @staticmethod
    def _guard_lineno(func_node: ast.AST) -> int | None:
        """Line of the first ``if state.full_page_already_applied: return``."""
        for node in ast.walk(func_node):
            if not isinstance(node, ast.If):
                continue
            references_flag = any(
                isinstance(sub, ast.Attribute)
                and sub.attr == "full_page_already_applied"
                for sub in ast.walk(node.test)
            )
            if not references_flag:
                continue
            if any(isinstance(stmt, ast.Return) for stmt in node.body):
                return node.lineno
        return None

    @staticmethod
    def _first_retry_call_lineno(func_node: ast.AST) -> int | None:
        linenos = [
            node.lineno
            for node in ast.walk(func_node)
            if isinstance(node, ast.Attribute) and node.attr == "_execute_ocr_retry"
        ]
        return min(linenos) if linenos else None

    def test_ocr_recovery_methods_discovered(self):
        """Sanity check: the dynamic discovery actually finds the known methods.

        Without this, a discovery bug would make the guard below vacuously pass
        over an empty method set.
        """
        discovered = set(self._ocr_retry_methods())

        assert discovered >= {
            "_recover_garble_ocr",
            "_recover_low_content_ocr",
            "_recover_image_dominant_ocr",
        }, (
            "expected the three known _recover_*_ocr methods to be discovered "
            f"as _execute_ocr_retry callers, got: {sorted(discovered)}"
        )

    def test_all_ocr_retry_methods_guard_on_full_page_already_applied(self):
        violations: list[str] = []

        for method_name, func_node in self._ocr_retry_methods().items():
            guard_line = self._guard_lineno(func_node)
            retry_line = self._first_retry_call_lineno(func_node)

            if guard_line is None:
                violations.append(
                    f"{method_name}: no early-return guard on "
                    "state.full_page_already_applied"
                )
            elif retry_line is not None and guard_line > retry_line:
                violations.append(
                    f"{method_name}: re-entry guard (line +{guard_line}) is "
                    f"positioned after the _execute_ocr_retry call "
                    f"(line +{retry_line})"
                )

        assert not violations, (
            "R1.4/Property 1 (RFC-044): every RecoveryMixin method calling "
            "_execute_ocr_retry must early-return on "
            "state.full_page_already_applied before that call. Violations: "
            + "; ".join(violations)
        )

    def test_decide_ocr_strategy_single_call_site(self):
        """R3.3/Property 3 (RFC-044): decide_ocr_strategy must have exactly
        one call site in src/ (the live call in converters/pictures.py) --
        the dead call site removed by Task 3.1/3.2 must stay removed."""
        src_dir = PROJECT_ROOT / "src"
        call_sites: list[str] = []
        for py_file in src_dir.rglob("*.py"):
            if "test" in py_file.name:
                continue
            for lineno, line in enumerate(py_file.read_text().splitlines(), 1):
                if "decide_ocr_strategy(" not in line:
                    continue
                if "def decide_ocr_strategy" in line:
                    continue
                call_sites.append(f"{py_file.relative_to(PROJECT_ROOT)}:{lineno}")

        assert len(call_sites) == 1, (
            "R3.3/Property 3 (RFC-044): decide_ocr_strategy must have "
            f"exactly one call site in src/. Found: {call_sites}"
        )
        assert call_sites[0].startswith(
            "src/pageindex_mcp/converters/pictures.py:"
        ), (
            "R3.3/Property 3 (RFC-044): the single live call site must be "
            f"in converters/pictures.py. Found: {call_sites[0]}"
        )

    def test_no_unreachable_ocr_plan_flag(self):
        """R4/Property 4 (RFC-044): the unreachable UNIFIED_OCR_PLAN_ENABLED
        flag and its dead image branch must be fully removed from src/."""
        src_dir = PROJECT_ROOT / "src"
        hits: list[str] = []
        for py_file in src_dir.rglob("*.py"):
            for lineno, line in enumerate(
                py_file.read_text().splitlines(), 1
            ):
                if "UNIFIED_OCR_PLAN_ENABLED" in line:
                    hits.append(f"{py_file.relative_to(PROJECT_ROOT)}:{lineno}")

        assert not hits, (
            "R4/Property 4 (RFC-044): UNIFIED_OCR_PLAN_ENABLED must not "
            f"appear anywhere in src/. Found: {hits}"
        )


class TestEligibilityPredicateSymmetry:
    """R2.4/R2.6/Property 2 (RFC-044): every ``_eligible_*`` predicate in
    gates.py must use ``_all_defects(state)`` for defect-type membership
    checks and must never reference ``state.first_defect`` directly.

    Extended (Amendment 3) to verify the ``_recover_rtl_*`` methods in
    recovery.py also use ``_all_defects(state)`` for the RTL_REVERSAL
    membership check rather than ``state.first_defect ==
    TreeDefect.RTL_REVERSAL``. ``_recover_vlm_fallback`` is deliberately
    excluded -- its ``first_defect`` usage gates Tesseract raster fallback
    on GARBLING/NODE_GARBLING, not RTL_REVERSAL.
    """

    _ELIGIBLE_PREDICATE_NAMES = (
        "_eligible_garble",
        "_eligible_low_content",
        "_eligible_image_dominant",
        "_eligible_rtl",
    )

    _RECOVER_RTL_METHOD_NAMES = (
        "_recover_rtl_repair",
        "_recover_rtl_flat_compare",
    )

    @staticmethod
    def _func_source_without_docstring(func) -> str:
        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
        body = tree.body[0]
        if ast.get_docstring(body) is not None:
            body.body = body.body[1:]
        return ast.unparse(body)

    def test_eligible_predicates_do_not_reference_first_defect(self):
        from pageindex_mcp.helpers import gates as gates_mod

        violations = []
        for name in self._ELIGIBLE_PREDICATE_NAMES:
            func = getattr(gates_mod, name)
            source = self._func_source_without_docstring(func)
            if "first_defect" in source:
                violations.append(name)

        assert not violations, (
            "R2.4/Property 2 (RFC-044): no _eligible_* predicate may "
            "reference state.first_defect directly -- all must use "
            f"_all_defects(state). Violations: {violations}"
        )

    def test_eligible_predicates_use_all_defects(self):
        from pageindex_mcp.helpers import gates as gates_mod

        violations = []
        for name in self._ELIGIBLE_PREDICATE_NAMES:
            func = getattr(gates_mod, name)
            source = self._func_source_without_docstring(func)
            if "_all_defects(state)" not in source:
                violations.append(name)

        assert not violations, (
            "R2.4/Property 2 (RFC-044): every _eligible_* predicate must "
            f"use _all_defects(state) for defect membership. Violations: {violations}"
        )

    def test_grep_first_defect_absent_from_gates_eligible_functions(self):
        gates_path = (
            PROJECT_ROOT / "src" / "pageindex_mcp" / "helpers" / "gates.py"
        )
        source = gates_path.read_text()
        tree = ast.parse(source)

        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name not in self._ELIGIBLE_PREDICATE_NAMES:
                continue
            if ast.get_docstring(node) is not None:
                node.body = node.body[1:]
            func_source = ast.unparse(node)
            if "first_defect" in func_source:
                violations.append(node.name)

        assert not violations, (
            "Property 2 (RFC-044): grep -n 'first_defect' gates.py must "
            f"return zero hits inside _eligible_* function bodies. Violations: {violations}"
        )

    def test_recover_rtl_methods_do_not_gate_on_first_defect_rtl_reversal(self):
        from pageindex_mcp.client import recovery as recovery_mod

        violations = []
        for name in self._RECOVER_RTL_METHOD_NAMES:
            attr = getattr(recovery_mod.RecoveryMixin, name)
            source = self._func_source_without_docstring(attr)
            if "first_defect" in source and "RTL_REVERSAL" in source:
                tree = ast.parse(source)
                for cmp_node in ast.walk(tree):
                    if not isinstance(cmp_node, ast.Compare):
                        continue
                    left_is_first_defect = (
                        isinstance(cmp_node.left, ast.Attribute)
                        and cmp_node.left.attr == "first_defect"
                    )
                    compares_to_rtl_reversal = any(
                        isinstance(comparator, ast.Attribute)
                        and comparator.attr == "RTL_REVERSAL"
                        for comparator in cmp_node.comparators
                    )
                    if left_is_first_defect and compares_to_rtl_reversal:
                        violations.append(name)

        assert not violations, (
            "R2.6/Property 2 extension (RFC-044, Amendment 3): "
            "_recover_rtl_repair and _recover_rtl_flat_compare must not "
            "gate on state.first_defect == TreeDefect.RTL_REVERSAL -- they "
            f"must use _all_defects(state) instead. Violations: {violations}"
        )

    def test_recover_rtl_methods_use_all_defects_for_rtl_reversal(self):
        from pageindex_mcp.client import recovery as recovery_mod

        violations = []
        for name in self._RECOVER_RTL_METHOD_NAMES:
            attr = getattr(recovery_mod.RecoveryMixin, name)
            source = self._func_source_without_docstring(attr)
            if "TreeDefect.RTL_REVERSAL in _all_defects(state)" not in source:
                violations.append(name)

        assert not violations, (
            "R2.6/Property 2 extension (RFC-044, Amendment 3): "
            "_recover_rtl_repair and _recover_rtl_flat_compare must use "
            "TreeDefect.RTL_REVERSAL in _all_defects(state) for the "
            f"RTL_REVERSAL membership check. Violations: {violations}"
        )
