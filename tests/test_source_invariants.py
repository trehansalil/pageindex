# ALLOW-NEW-TEST-FILE: replaces six source-invariant guard files with tests of the gate script
"""Tests for the source-invariant gate and the few invariants that cannot be static.

``scripts/gates/source_invariants.py`` now owns every *source-code* invariant
that six pytest files used to assert -- 202 collected tests whose subject was
the repository's own text rather than any runtime behaviour.  Those belong in
the ``static`` gate: they run without a pytest collection slot each, report
every violation in one pass, and fail with a ``path:line: id: message`` line.

What remains here is the correct residue:

1. Tests of the *script*, because the script is now the thing under test --
   it must be green on this tree, it must fail loudly and name the invariant
   when fed a violation, and its trickiest helper (static binding resolution,
   PEP 562 proxies included) needs unit coverage.
2. The handful of invariants that genuinely cannot be decided statically,
   because they read values only the imported package can produce: the
   decision-point registry, the installed logging handler, the arq
   per-function timeout, live ``PipelineConfig`` thresholds, and the RFC-045
   facade *measurement* pins.

Replaced files (ICR-97 test-budget consolidation):
``test_architecture_guards.py`` (79), ``test_facade_surface_guard.py`` (98),
``test_rfc042_measurement_guard.py`` (8), ``test_rfc045_facade_measurement.py``
(5), ``test_no_naive_block_text.py`` (1), ``test_rfc_lifecycle_lint.py`` (11).
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import textwrap

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
GATE_SCRIPT = PROJECT_ROOT / "scripts" / "gates" / "source_invariants.py"


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(GATE_SCRIPT, "source_invariants_gate")
rfc_lifecycle_lint = _load(
    PROJECT_ROOT / "scripts" / "rfc_lifecycle_lint.py", "rfc_lifecycle_lint"
)
facade_measure = _load(
    PROJECT_ROOT / "scripts" / "facade_surface_measure.py", "facade_surface_measure"
)


def _with_root(tmp_root, fn):
    """Run *fn* with the gate pointed at *tmp_root*, then restore and reset."""
    caches = (gate.read, gate.tree_of, gate.src_files, gate.module_bindings, gate.declared_all)
    original = gate.ROOT
    gate.ROOT = tmp_root
    for cached in caches:
        cached.cache_clear()
    try:
        return fn()
    finally:
        gate.ROOT = original
        for cached in caches:
            cached.cache_clear()


# ---------------------------------------------------------------------------
# 1. The gate script itself
# ---------------------------------------------------------------------------


def test_gate_is_green_on_this_tree():
    """The tree is the baseline: a violation here means the *script* is wrong."""
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=300,
    )
    assert result.returncode == 0, (
        "source_invariants.py reports violations on a tree that is supposed to be "
        f"clean:\n{result.stdout}\n{result.stderr}"
    )
    assert "PASS gate=source-invariants" in result.stdout


def test_every_registered_check_has_a_unique_id():
    """A check defined but never registered guards nothing, and two checks
    sharing an id make the failure output ambiguous."""
    ids = [invariant_id for invariant_id, _ in gate.CHECKS]
    assert len(ids) >= 30, f"only {len(ids)} checks registered -- did a block fail to import?"
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates, f"duplicate invariant ids: {duplicates}"


def test_gate_sweeps_are_not_vacuous():
    """Each repo-wide sweep must actually see the code it claims to scan.

    Every one of these counts was a separate assertion in the deleted files,
    and each exists because a sweep that silently stops finding anything
    passes forever.
    """
    measured = {
        "src files": len(gate.src_files()),
        "frozen packages": len(gate.FROZEN_SURFACE),
        "consumer refs": len(gate.consumer_refs()),
        "decision call sites": len(gate.decision_call_sites()),
        "pipeline-config env vars": len(gate._pipeline_config_owned_env_vars()),
    }
    floors = {
        "src files": 40,
        "frozen packages": 9,
        "consumer refs": 40,
        "decision call sites": 100,
        "pipeline-config env vars": 30,
    }
    too_few = {k: v for k, v in measured.items() if v < floors[k]}
    assert not too_few, f"sweep(s) below their vacuity floor: {too_few} (floors: {floors})"


def test_gate_reports_and_fails_on_a_synthetic_violation(tmp_path):
    """Fed a violating tree, the gate exits 1 and names the invariant."""
    pkg = tmp_path / "src" / "pageindex_mcp" / "helpers"
    pkg.mkdir(parents=True)
    (tmp_path / "src" / "pageindex_mcp" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "flat.py").write_text(
        textwrap.dedent(
            """
            def measure(block):
                return block.get("text", "")
            """
        )
    )
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 1
    assert "naive-block-text" in result.stdout
    assert "helpers/flat.py:3" in result.stdout
    assert "FAIL gate=source-invariants" in result.stderr


def test_gate_ignores_the_banned_pattern_in_comments_and_docstrings(tmp_path):
    """Prose that *describes* a banned pattern is not an instance of it --
    the deleted guards had to learn this twice (garble.py's docstring names
    ``os.environ``; log_config.py's names ``logging.basicConfig``)."""
    pkg = tmp_path / "src" / "pageindex_mcp" / "helpers"
    pkg.mkdir(parents=True)
    (pkg / "flat.py").write_text(
        '"""Never write block.get("text", "") -- use block_text()."""\n\n'
        '# block.get("text", "") is the defect this module replaced.\n'
        "def measure(block):\n"
        "    return block_text(block)\n"
    )
    offenders = _with_root(tmp_path, lambda: list(gate.check_naive_block_text()))
    assert not offenders, f"comment/docstring prose reported as a violation: {offenders}"


def test_quarantine_prefix_confined_catches_a_new_reader(tmp_path):
    """RFC-049 R4 AC3 / Task 7.10: a module other than storage/documents.py
    that names the quarantine prefix is reported.

    This is the guard's whole point -- the served-surface audit is only true
    of today's call sites, so the invariant has to fail when someone adds a
    reader tomorrow."""
    pkg = tmp_path / "src" / "pageindex_mcp" / "storage"
    pkg.mkdir(parents=True)
    (pkg / "documents.py").write_text('PREFIX = "quarantine/"\n')
    (tmp_path / "src" / "pageindex_mcp" / "server.py").write_text(
        'def peek(client):\n    return client.list_objects(prefix="quarantine/")\n'
    )

    offenders = _with_root(tmp_path, lambda: list(gate.check_quarantine_prefix_confined()))

    assert len(offenders) == 1, f"expected exactly the server.py reader, got {offenders}"
    assert offenders[0].path == "src/pageindex_mcp/server.py"
    assert offenders[0].line == 2
    assert offenders[0].invariant == "quarantine-prefix-confined"


def test_quarantine_prefix_confined_allows_the_owning_module(tmp_path):
    """storage/documents.py owns the prefix: registration, the manifest and
    the erasure step all name it there and must stay silent."""
    pkg = tmp_path / "src" / "pageindex_mcp" / "storage"
    pkg.mkdir(parents=True)
    (pkg / "documents.py").write_text(
        'register_storage_prefix("quarantine/")\n_MAP = {"quarantine/": ("quarantine",)}\n'
    )

    offenders = _with_root(tmp_path, lambda: list(gate.check_quarantine_prefix_confined()))

    assert not offenders, f"the owning module was reported: {offenders}"


# ---------------------------------------------------------------------------
# 2. module_bindings -- the trickiest parsing helper
# ---------------------------------------------------------------------------


class TestModuleBindings:
    """``module_bindings`` answers "does ``pkg.name`` resolve?" from source
    text alone, which is what lets the facade guards run without importing
    the package.  Its hard cases are aliased imports, conditional imports,
    tuple targets and PEP 562 ``__getattr__`` proxies."""

    def test_static_forms_are_all_collected(self, tmp_path):
        pkg = tmp_path / "src" / "pageindex_mcp" / "demo"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            textwrap.dedent(
                """
                from __future__ import annotations
                import os
                import collections.abc
                import json as _json
                from .thing import Widget, helper as _helper
                CONST = 1
                A, B = 2, 3
                TYPED: int = 4

                try:
                    from .optional import maybe
                except ImportError:
                    maybe = None


                def fn():
                    hidden_local = 1
                    return hidden_local


                class Klass:
                    attribute = 1
                """
            )
        )
        (pkg / "thing.py").write_text("Widget = 1\ndef helper():\n    return 1\n")

        bound = _with_root(tmp_path, lambda: gate.module_bindings("pageindex_mcp.demo"))

        expected = {
            "os",
            "collections",
            "_json",
            "Widget",
            "_helper",
            "CONST",
            "A",
            "B",
            "TYPED",
            "maybe",
            "fn",
            "Klass",
            "thing",
        }
        assert expected <= bound, f"missing bindings: {sorted(expected - bound)}"
        assert "hidden_local" not in bound, "function locals are not module bindings"
        assert "attribute" not in bound, "class attributes are not module bindings"

    def test_pep562_getattr_proxy_names_count_as_bound(self):
        """``registry/__init__.py`` serves ``_pool`` and ``_KNOWN_FACETS``
        through a module-level ``__getattr__`` so ``registry._pool = x`` writes
        through to the submodule.  A resolver that only looks at assignment
        targets calls those unbound and condemns a live export."""
        bound = gate.module_bindings("pageindex_mcp.registry")
        assert {"_pool", "_KNOWN_FACETS"} <= bound

    def test_unknown_module_resolves_to_nothing(self):
        assert gate.module_bindings("pageindex_mcp.not_a_real_module") == frozenset()
        assert gate.module_path("os.path") is None


# ---------------------------------------------------------------------------
# 3. Invariants that cannot be decided statically
# ---------------------------------------------------------------------------


class TestRuntimeOnlyInvariants:
    """Each of these reads a value only the imported package can produce, so
    it stays in pytest rather than moving to the static gate."""

    def test_decision_call_sites_agree_with_the_registry(self):
        """R12.7/R12.8 (RFC-046 D12). The registry is a contract, not
        documentation: an entry with no emitter is a decision logtrace will
        never show, an event absent from it bypasses review, and a
        content-bearing attr key is a PII leak (Hard Rule 3), not a style
        preference -- a node title can name an insured party.
        """
        from pageindex_mcp.obs import DECISION_EVENTS, is_content_attr, point_for

        sites = gate.decision_call_sites()
        emitted = {s["event"] for s in sites}

        problems: dict[str, list] = {
            "events emitted but absent from DECISION_POINTS": [
                (s["path"], s["line"], s["event"])
                for s in sites
                if s["event"] not in DECISION_EVENTS
            ],
            "registered decision points that nothing emits": sorted(DECISION_EVENTS - emitted),
            "content-bearing attrs at a decision() call site": [
                (s["path"], s["event"], key)
                for s in sites
                for key in s["attr_keys"]
                if is_content_attr(key)
            ],
            "attrs keys not declared in DECISION_POINTS": [
                (s["path"], s["event"], extra)
                for s in sites
                if (point := point_for(s["event"])) is not None
                and (extra := [k for k in s["attr_keys"] if k not in point.attrs])
            ],
        }
        assert not any(problems.values()), {k: v for k, v in problems.items() if v}

    def test_configured_handler_writes_to_stderr(self):
        """R12.11: ``converters_cli`` reserves stdout for exactly two JSON
        lines. A handler defaulting to stdout fails every job with
        'invalid JSON on stdout'."""
        import logging

        from pageindex_mcp.obs import configure
        from pageindex_mcp.obs.constants import HANDLER_MARKER

        previous = list(logging.getLogger().handlers)
        try:
            configure()
            handlers = [
                h for h in logging.getLogger().handlers if getattr(h, HANDLER_MARKER, False)
            ]
            assert len(handlers) == 1, "configure() must install exactly one handler"
            assert handlers[0].stream is sys.stderr
        finally:
            root = logging.getLogger()
            for h in list(root.handlers):
                root.removeHandler(h)
            for h in previous:
                root.addHandler(h)

    def test_arq_function_timeout_is_a_non_binding_backstop(self):
        """RFC-046 D11 (task 3.12): the arq per-function timeout on
        ``process_document_job`` must be >= ``MAX_EFFECTIVE_TIMEOUT`` so arq
        never cancels before the dynamic effective_timeout fires."""
        from pageindex_mcp.worker import WorkerSettings
        from pageindex_mcp.worker.constants import MAX_EFFECTIVE_TIMEOUT

        funcs = WorkerSettings.functions
        assert len(funcs) >= 1
        job_func = funcs[0]
        assert hasattr(job_func, "timeout_s"), (
            "process_document_job must be wrapped with arq.func() to set a per-function "
            "timeout -- a bare coroutine inherits the class-level job_timeout, which is too tight"
        )
        assert job_func.timeout_s >= MAX_EFFECTIVE_TIMEOUT, (
            f"arq per-function timeout ({job_func.timeout_s}s) is tighter than "
            f"MAX_EFFECTIVE_TIMEOUT ({MAX_EFFECTIVE_TIMEOUT}s) -- arq will cancel before "
            "the dynamic effective_timeout fires"
        )

    def test_verdict_and_gate_thresholds_have_not_widened(self):
        """Non-Goal 2 / Property 8 (RFC-046). Threshold widening masks
        extraction defects -- the anti-pattern ``audit/zones/_index.md`` names.
        Change the pin here only when the widening is deliberate and reviewed.
        """
        from pageindex_mcp.config import CATEGORY_BC_PROMOTION_THRESHOLD, PipelineConfig

        pinned = {
            "hard_fail_max_leaf_ratio": 0.75,
            "pass_max_leaf_ratio": 0.30,
            "garble_window_ratio_threshold": 0.05,
            "cat_bc_promotion_threshold": 0.17,
            "min_image_promoted_chars": 500,
            "min_flat_promotion_chars": 500,
            "small_doc_leaf_ratio_bound_low": 0.20,
            "small_doc_leaf_ratio_bound_high": 0.40,
            "min_marginal_chars": 50,
            "cat_a_max_leaf_ratio": 0.15,
            "cat_a_max_ocr_noise": 0.005,
            "small_doc_min_chars": 100,
            "small_doc_max_chars": 15000,
            "rfc029_min_scanned_density_floor": 1200.0,
            "rfc029_min_scanned_density_floor_arabic": 800.0,
        }
        cfg = PipelineConfig.from_env()
        drift = {}
        for field, expected in pinned.items():
            actual = (
                CATEGORY_BC_PROMOTION_THRESHOLD
                if field == "cat_bc_promotion_threshold"
                else getattr(cfg, field)
            )
            if actual != expected:
                drift[field] = {"expected": expected, "actual": actual}
        assert not drift, f"verdict/gate threshold(s) moved from pre-RFC values: {drift}"

    def test_table_blocks_measure_through_block_text(self):
        """RFC-042 D5-R5.1: flat table blocks carry content in
        ``row_records``/``headers``/``rows`` and have no ``text`` key at all.
        Each must yield a non-zero char count through ``block_text``, and the
        legacy wrappers must delegate to it identically -- the 96% content
        miss on GHV-TKV-Tarif was exactly this gap.
        """
        from pageindex_mcp.helpers import (
            BlockTextPurpose,
            _flat_block_primary_text,
            _flat_search_text,
            block_text,
            doc_text,
        )

        cases = {
            "row_records": {"role": "table", "row_records": ["alpha row", "beta row"]},
            "headers_only": {"role": "table", "headers": ["Col A", "Col B"], "row_records": []},
            "headers_and_rows": {
                "role": "table",
                "headers": ["H1", "H2"],
                "rows": [["a", "b"], ["c", "d"]],
            },
        }
        problems = []
        for name, block in cases.items():
            assert "text" not in block
            measured = block_text(block, BlockTextPurpose.CHAR_COUNT)
            if not measured:
                problems.append(f"{name}: block_text returned no content")
            if _flat_block_primary_text(block) != measured:
                problems.append(f"{name}: _flat_block_primary_text diverges from block_text")
        if block_text(cases["row_records"], BlockTextPurpose.CHAR_COUNT) != "alpha row\nbeta row":
            problems.append("row_records are not joined verbatim")
        if "Col A" not in block_text(cases["headers_only"], BlockTextPurpose.CHAR_COUNT):
            problems.append("header-only tables lose their header text")

        data = {
            "blocks": [
                {"role": "title", "text": "Section"},
                {"role": "table", "row_records": ["row one", "row two"]},
            ]
        }
        search = _flat_search_text(data)
        if search != doc_text(data, BlockTextPurpose.SEARCH):
            problems.append("_flat_search_text diverges from doc_text")
        if "row one" not in search or "row two" not in search:
            problems.append("_flat_search_text drops table content")
        assert not problems, problems


# ---------------------------------------------------------------------------
# 4. Sibling gate scripts these files also covered
# ---------------------------------------------------------------------------


def _write_rfc(dir_path, filename, rfc_id, status, extra_body=""):
    path = dir_path / filename
    path.write_text(
        f'---\nid: "{rfc_id}"\ntitle: "Test RFC"\nstatus: {status}\n---\n\n'
        f"## Overview\n\nTest body.\n{extra_body}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def rfc_env(tmp_path):
    rfcs_dir = tmp_path / "agents" / "rfcs"
    tasks_dir = tmp_path / "agents" / "tasks"
    zones_dir = tmp_path / "audit" / "zones"
    for d in (rfcs_dir, tasks_dir, zones_dir):
        d.mkdir(parents=True)
    return rfcs_dir, tasks_dir, zones_dir / "ZONE_OWNERSHIP.yaml"


def test_rfc_lifecycle_lint_rules(rfc_env):
    """RFC-041 D8 / Property 8: the lifecycle lint's rule table, asserted in
    one pass so every misfiring rule is reported at once.

    ``prose_resolved_oq`` exists because RFC-045 OQ2 read "...cannot be
    resolved by either package's owner alone" -- genuinely open, but a
    case-insensitive 'resolved' check skipped it, so the question never
    surfaced in CI.
    """
    rfcs_dir, tasks_dir, zone_file = rfc_env
    gate_task = '- [ ] <a id="91"></a>9.1 **[GATE]** Scoped re-ingest and re-measurement'
    later_task = '- [x] <a id="92"></a>9.2 Promote bidi_coherence_enforce to blocking'

    scenarios: dict[str, tuple[dict, str, bool]] = {
        # name: (setup, rule, rule expected to fire)
        "skipped_gate": ({"tasks": [gate_task, later_task]}, "skipped-gate", True),
        "gate_checked_first": (
            {"tasks": [gate_task.replace("[ ]", "[x]"), later_task]},
            "skipped-gate",
            False,
        ),
        "all_done_draft": (
            {"tasks": ['- [x] <a id="11"></a>1.1 First', '- [x] <a id="12"></a>1.2 Second']},
            "all-tasks-done-draft",
            True,
        ),
        "unresolved_oq": (
            {"body": "\n## Open Questions\n\n1. **Unresolved thing:** still undecided.\n"},
            "unresolved-open-question",
            True,
        ),
        "resolved_oq": (
            {"body": "\n## Open Questions\n\n1. **Settled:** RESOLVED, see RFC-997.\n"},
            "unresolved-open-question",
            False,
        ),
        "prose_resolved_oq": (
            {
                "body": "\n## Open Questions\n\n1. **Wholesale or per package?** This "
                "cannot be resolved by either owner alone.\n"
            },
            "unresolved-open-question",
            True,
        ),
        "orphaned_zone": (
            {"status": "implemented", "zone": ("RFC-998", "null")},
            "orphaned-zone",
            True,
        ),
        "zone_transferred": (
            {"status": "implemented", "zone": ("RFC-998", "RFC-999"), "successor": True},
            "orphaned-zone",
            False,
        ),
        "zone_owner_still_open": ({"zone": ("RFC-998", "null")}, "orphaned-zone", False),
    }

    wrong: list[str] = []
    for name, (setup, rule, expected) in scenarios.items():
        for stale in list(rfcs_dir.iterdir()) + list(tasks_dir.iterdir()):
            stale.unlink()
        if zone_file.exists():
            zone_file.unlink()

        _write_rfc(
            rfcs_dir,
            "998-owner.md",
            "RFC-998",
            setup.get("status", "draft"),
            setup.get("body", ""),
        )
        if setup.get("successor"):
            _write_rfc(rfcs_dir, "999-successor.md", "RFC-999", "draft")
        if "tasks" in setup:
            (tasks_dir / "tasks-rfc998-test.md").write_text(
                "\n".join(setup["tasks"]) + "\n", encoding="utf-8"
            )
        if "zone" in setup:
            owner, successor = setup["zone"]
            zone_file.write_text(
                'zones:\n  zone_x:\n    name: "Zone"\n'
                f"    owning_rfc: {owner}\n    resolved: false\n"
                f"    successor_rfc: {successor}\n",
                encoding="utf-8",
            )

        violations = rfc_lifecycle_lint.lint(rfcs_dir, tasks_dir, zone_file)
        fired = {v.rule for v in violations}
        blocking = {v.rule for v in violations if v.severity == "blocking"}
        if (rule in fired) is not expected:
            wrong.append(f"{name}: expected {rule} present={expected}, fired={sorted(fired)}")
        if rule in ("skipped-gate", "orphaned-zone"):
            if expected and rule not in blocking:
                wrong.append(f"{name}: {rule} must be blocking, got {sorted(blocking)}")
            if not expected and blocking:
                wrong.append(f"{name}: expected no blocking violations, got {sorted(blocking)}")

    assert not wrong, wrong


def test_rfc_lifecycle_lint_sees_the_real_repo_state(tmp_path):
    """The lint must load the real ZONE_OWNERSHIP manifest and real task files
    and still catch a skipped gate in them -- a lint that only ever runs against
    synthetic fixtures proves nothing about CI. The repo's own violations get
    fixed over time, so reopen a real closed gate in a copy of agents/tasks/
    rather than depending on one happening to exist."""
    import shutil

    zone_file = PROJECT_ROOT / "audit" / "zones" / "ZONE_OWNERSHIP.yaml"
    manifest = rfc_lifecycle_lint.load_zone_ownership(zone_file)
    assert manifest["zones"]["zone_2"]["successor_rfc"] == "RFC-046"

    tasks = tmp_path / "tasks"
    shutil.copytree(PROJECT_ROOT / "agents" / "tasks", tasks)
    rfc033 = tasks / "tasks-rfc033-run15-reingestion-quality-fixes.md"
    closed_gate = '- [x] <a id="91-scoped-reingest-and-remeasure"></a>'
    text = rfc033.read_text(encoding="utf-8")
    assert closed_gate in text, "fixture drifted: RFC-033 gate 9.1 is no longer a closed gate"
    rfc033.write_text(text.replace(closed_gate, closed_gate.replace("[x]", "[ ]")), encoding="utf-8")

    violations = rfc_lifecycle_lint.lint(PROJECT_ROOT / "agents" / "rfcs", tasks, zone_file)
    reopened = [
        v for v in violations
        if v.rule == "skipped-gate" and v.severity == "blocking" and "9.1" in str(v)
    ]
    assert reopened, f"lint missed the reopened real gate 9.1; got {violations}"


def test_facade_disposition_measurement_matches_the_rfc045_pins():
    """RFC-045 R2: the narrow coupling rule preserves the removal set (48 of
    59) while the broad "referenced anywhere in a kept sibling" rule collapses
    it (10 of 59) -- the comparison that sank the earlier 59 -> 7 proposal and
    the evidence the whole requirement rests on.

    The pins describe the PRE-SHRINK facade.  Once RFC-045 execution removes
    entries, ``__all__`` changes under the measurement's feet, so this skips
    rather than failing confusingly mid-wave; updating the pins is an explicit
    task in ``agents/tasks/tasks-rfc045-package-facade-surface.md``.
    """
    removals = facade_measure.load_removals()
    assert len(removals) == 59, "section B's candidate population changed"
    assert len(set(removals)) == 59, "section B has duplicate rows"

    for pkg in {p for p, _ in removals}:
        all_names, _blocks = facade_measure.parse_init(pkg)
        if any(name not in all_names for p, name in removals if p == pkg):
            pytest.skip(
                "RFC-045 shrink has started: re-run scripts/facade_surface_measure.py and "
                "update these pins (see tasks-rfc045-package-facade-surface.md)."
            )

    results = facade_measure.measure(removals)
    narrow = [r for r in results if r["narrow_disposition"] == "REMOVE"]
    broad = [r for r in results if r["broad_disposition"] == "REMOVE"]
    flips = {
        (r["package"], r["name"]): r["narrow_reasons"]
        for r in results
        if r["narrow_disposition"] == "KEEP"
    }
    per_package: dict[str, int] = {}
    for row in narrow:
        per_package[row["package"]] = per_package.get(row["package"], 0) + 1

    expected_flips = {
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
    assert {
        "narrow_remove": len(narrow),
        "broad_remove": len(broad),
        "flips": flips,
        "per_package": per_package,
    } == {
        "narrow_remove": 48,
        "broad_remove": 10,
        "flips": expected_flips,
        "per_package": {
            "client": 2,
            "converters": 19,
            "helpers": 11,
            "registry_backfill": 8,
            "storage": 1,
            "worker": 7,
        },
    }
