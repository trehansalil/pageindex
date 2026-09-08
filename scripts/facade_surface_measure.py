#!/usr/bin/env python3
"""RFC-045 Requirement 2 -- mechanical disposition measurement.

Loads the proposed facade removals from the audit manifest, resolves each to
its defining submodule file, and runs a set of narrow, mechanically-checkable
coupling signals against the CURRENT repo source (not the audit prose).
Reports which signal (if any) fires, the resulting disposition, and a
calibration run showing what happens under a much broader "referenced
anywhere in a kept sibling" rule.

The calibration exists to answer the question that stalled RFC-045: a broad
rule collapses the removal set the way the rejected per-import-block rule did
(59 -> 7), while the narrow rule preserves it. Both numbers are printed so a
reviewer can see the gap rather than take the choice on trust.

This script is the reproducible artifact behind RFC-045 Requirement 2. It is
read-only: it never edits a facade, it only measures one.

Usage:
    uv run python scripts/facade_surface_measure.py
    uv run python scripts/facade_surface_measure.py --json /tmp/results.json
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "pageindex_mcp"
MANIFEST = REPO / "audit" / "FACADE_SURFACE_MANIFEST_2026-09-07.md"

#: The manifest's section B candidate count, pinned so a manifest edit that
#: changes the population is a loud failure rather than a silently different
#: measurement under the same RFC requirement number.
EXPECTED_CANDIDATES = 59

#: Signals that constitute the narrow rule. ``string_registry`` is handled
#: separately because it carries evidence (the hit list), not just a bool.
NARROW_SIGNALS = ("flag_gate", "type_annotation", "arithmetic_operand", "cross_package")


# ---------------------------------------------------------------------------
# 1. The proposed removals, loaded from the manifest itself so this script
#    tracks the RFC's own list rather than a hand-copied one.
# ---------------------------------------------------------------------------
def load_removals(manifest_path: pathlib.Path = MANIFEST) -> list[tuple[str, str]]:
    """Return [(package, name)] parsed from manifest section B."""
    manifest = manifest_path.read_text()
    sec_b = manifest.split("## B. Proposed for removal")[1].split("## C. Kept")[0]

    removals: list[tuple[str, str]] = []
    current_pkg = None
    for line in sec_b.splitlines():
        m_pkg = re.match(r"### `(\w+)/__init__\.py`", line)
        if m_pkg:
            current_pkg = m_pkg.group(1)
            continue
        m_name = re.match(r"^\|\s*`([^`]+)`\s*\|", line)
        if m_name and current_pkg:
            removals.append((current_pkg, m_name.group(1)))
    return removals


# ---------------------------------------------------------------------------
# 2. The 23 split rows, as classified by empirical source reading.
#    package -> submodule file (relative to package dir) -> removed names in
#    that split row. Hand-built from reading __init__.py + the submodule
#    source directly, not trusted from the truncated manifest table.
# ---------------------------------------------------------------------------
SPLIT_ROWS = [
    # (row#, package, submodule_file, [removed names in this row])
    (
        1,
        "converters",
        "pictures.py",
        ["_IMAGE_ENRICH_CONCURRENCY", "_PAGE_ROTATION_DETECTION_ENABLED"],
    ),
    (2, "converters", "pictures.py", ["_PAGE_ROTATION_DETECTION_ENABLED"]),
    (3, "converters", "docling_conv.py", ["_CHUNKED_DOCLING_PER_CHUNK_TIMEOUT_S"]),
    (4, "converters", None, ["FuturesTimeoutError"]),  # concurrent.futures, string-keyed elsewhere
    (5, "converters", "types.py", ["StageRecord"]),
    (6, "converters", "script.py", ["BlobKind", "ScriptContext", "_word_has_reversed_morphology"]),
    (7, "converters", "pictures.py", ["_rasterize_rotate_page"]),
    (8, "converters", "ocr_langs.py", ["_LATIN_LANGS"]),
    (9, "converters", "docling_conv.py", ["_detect_pdf", "_pdf_inspector_available"]),
    (
        10,
        "helpers",
        "tables.py",
        [
            "_flat_is_pipe_row",
            "_flat_is_separator_row",
            "_flat_split_pipe_row",
            "_flat_verbalize_rows",
        ],
    ),
    (11, "helpers", "tree_validation.py", ["_count_empty_body_nodes", "_walk_leaves"]),
    (12, "helpers", "table_stitch.py", ["_looks_like_toc_page", "flag_empty_cells"]),
    (13, "helpers", "types.py", ["_GateFn"]),
    (14, "helpers", "rag.py", ["_rag_inner"]),
    (
        15,
        "worker",
        "registry_mirror.py",
        [
            "_VERDICT_RETRY_KEY_PREFIX",
            "_VERDICT_RETRY_TTL_S",
            "_enqueue_verdict_retry",
            "_mirror_bridged_incr",
            "_mirror_bridged_set",
        ],
    ),
    (16, "worker", "registry_mirror.py", ["_mirror_bridged_incr", "_mirror_bridged_set"]),
    (17, "worker", None, ["JOB_TTL", "KILL_GRACE_SECONDS"]),  # job.py / subprocess_mgr.py resp.
    (18, "worker", "lifecycle.py", ["_reconcile_registry_drift_cron"]),
    (19, "registry_backfill", "reconcile.py", ["_record_reconcile_heartbeat"]),
    (20, "registry_backfill", "backfill.py", ["main"]),
    (
        21,
        "registry_backfill",
        "backfill.py",
        ["_is_fat", "_load_meta", "_preflight_checks", "_prepare_metas"],
    ),
    (22, "storage", "verdict.py", ["SIDECAR_VERSION"]),
    (
        23,
        "converters",
        "headings.py",
        ["_collect_heading_pages", "_md_to_structure", "_VERDICT_RANK"],
    ),
]

#: Rows 4 and 17 hold names whose defining files differ within one row.
FILE_OVERRIDE = {
    ("worker", "JOB_TTL"): "job.py",
    ("worker", "KILL_GRACE_SECONDS"): "subprocess_mgr.py",
}


def _build_split_index() -> tuple[set[tuple[str, str]], dict[tuple[str, str], str | None]]:
    split_names: set[tuple[str, str]] = set()
    name_to_file: dict[tuple[str, str], str | None] = {}
    for _row, pkg, fname, names in SPLIT_ROWS:
        for n in names:
            split_names.add((pkg, n))
            name_to_file[(pkg, n)] = FILE_OVERRIDE.get((pkg, n), fname)
    return split_names, name_to_file


SPLIT_NAMES, NAME_TO_FILE = _build_split_index()


# ---------------------------------------------------------------------------
# 3. Resolve each candidate to its defining submodule + the package's current
#    __all__, so we know which siblings count as "kept".
# ---------------------------------------------------------------------------
def parse_init(pkg: str) -> tuple[set[str], list[tuple[int, str, list[str]]]]:
    """Return (all_names, import_blocks) for a package's ``__init__.py``.

    ``level`` is ``ast.ImportFrom.level``: 1 for ``from .mod import X`` (this
    package), 2 for ``from ..sibling import X`` (a different top-level
    package). ``node.module`` never carries the leading dots itself -- an
    earlier version of this script string-matched on ``.`` prefixes that ast
    strips out, which silently failed to resolve every single-dot import
    (i.e. most of them). Fixed to use ``node.level`` directly.
    """
    path = SRC / pkg / "__init__.py"
    tree = ast.parse(path.read_text())
    all_names: set[str] = set()
    blocks: list[tuple[int, str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "__all__" for t in node.targets
        ):
            all_names = {e.value for e in node.value.elts}
        if isinstance(node, ast.ImportFrom):
            blocks.append((node.level, node.module or "", [a.asname or a.name for a in node.names]))
    return all_names, blocks


def module_file_for(pkg: str, name: str, init_cache: dict) -> pathlib.Path | None:
    """Resolve the submodule file that defines ``name`` for ``pkg``."""
    override = NAME_TO_FILE.get((pkg, name))
    if override:
        return SRC / pkg / override
    _all, blocks = init_cache[pkg]
    for level, mod, names in blocks:
        if name not in names:
            continue
        if level >= 2:
            # Sibling top-level package, e.g. `from ..script import X`.
            return SRC / f"{mod}.py" if mod else None
        if level == 1:
            return SRC / pkg / f"{mod}.py"
    return None


# ---------------------------------------------------------------------------
# 4. AST coupling signals against the actual current source.
# ---------------------------------------------------------------------------
class FileFacts:
    """Module-level facts about one submodule, reused across candidates."""

    def __init__(self, path: pathlib.Path, kept_names: set[str]):
        self.path = path
        self.tree = ast.parse(path.read_text())
        self.kept_names = kept_names
        self.module_bool_names: set[str] = set()
        self.module_assign_values: dict[str, ast.AST] = {}
        self.cross_pkg_imports: set[str] = set()
        self._scan_module_level()

    def _scan_module_level(self) -> None:
        for node in self.tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                tgt = node.targets[0].id
                self.module_assign_values[tgt] = node.value
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, bool):
                    self.module_bool_names.add(tgt)
            if isinstance(node, ast.ImportFrom) and node.module:
                self._record_cross_package(node)

    def _record_cross_package(self, node: ast.ImportFrom) -> None:
        """Record names imported from a DIFFERENT top-level package.

        ``level >= 2`` means ``from ..<pkg> import X``. ``node.module`` never
        carries the leading dots -- ast tracks those in ``node.level`` -- so
        level is the only correct check.
        """
        if node.level < 2:
            return
        own_pkg = self.path.parent.name
        other = (node.module or "").split(".")[0]
        if other and other != own_pkg:
            for a in node.names:
                self.cross_pkg_imports.add(a.asname or a.name)

    def kept_defs(self):
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (
                node.name in self.kept_names
            ):
                yield node


def names_referenced(node: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            out.add(n.value.id)
    return out


def _annotation_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def is_flag_gate(name: str, ff: FileFacts, kept_def: ast.AST) -> bool:
    """``if NAME:`` / ``if not NAME:`` inside a kept def, where NAME is a
    module-level bool constant (or is named like a feature flag)."""
    looks_flagish = (
        name in ff.module_bool_names or name.endswith("_ENABLED") or name.endswith("_available")
    )
    if not looks_flagish:
        return False
    for n in ast.walk(kept_def):
        if not isinstance(n, ast.If):
            continue
        test = n.test
        if isinstance(test, ast.Name) and test.id == name:
            return True
        if (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Name)
            and test.operand.id == name
        ):
            return True
    return False


def _annotated_nodes(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    all_args = (
        list(fn.args.posonlyargs)
        + list(fn.args.args)
        + list(fn.args.kwonlyargs)
        + ([fn.args.vararg] if fn.args.vararg else [])
        + ([fn.args.kwarg] if fn.args.kwarg else [])
    )
    ann = [fn.returns] if fn.returns else []
    return ann + [a.annotation for a in all_args if a.annotation]


def is_type_annotation(name: str, kept_def: ast.AST, is_class: bool) -> bool:
    """NAME used as a return type / arg annotation / dataclass field annotation
    on the kept def itself.

    A ``NAME(...)`` call inside the body counts as type-construction only when
    NAME is itself a class -- otherwise every private helper's ordinary call
    site false-positives here, which is exactly the over-broad signal this
    rule exists to avoid.
    """
    if isinstance(kept_def, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for a in _annotated_nodes(kept_def):
            if name in _annotation_names(a):
                return True
    if isinstance(kept_def, ast.ClassDef):
        for n in kept_def.body:
            if (
                isinstance(n, ast.AnnAssign)
                and n.annotation is not None
                and (name in _annotation_names(n.annotation))
            ):
                return True
    if is_class:
        for n in ast.walk(kept_def):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name:
                return True
    return False


def definition_kind(name: str, mod_file: pathlib.Path | None) -> str:
    """'class' | 'function' | 'const' | 'unknown', from the name's own
    top-level definition in ``mod_file``."""
    if not mod_file or not mod_file.exists():
        return "unknown"
    for node in ast.parse(mod_file.read_text()).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return "class"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "function"
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return "const"
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if (a.asname or a.name) == name and a.name != name:
                    # Aliased import, e.g. `TimeoutError as FuturesTimeoutError`.
                    return "class"
    return "unknown"


def is_arithmetic_operand(name: str, kept_def: ast.AST) -> bool:
    """Genuine arithmetic coupling in the EXECUTABLE body only.

    Excludes args/returns/decorators so a ``X | None`` PEP-604 union
    annotation (also a BinOp at the AST level) is not double-counted as
    arithmetic; that case is caught by :func:`is_type_annotation` instead.
    """
    body = (
        kept_def.body
        if isinstance(kept_def, (ast.FunctionDef, ast.AsyncFunctionDef))
        else [kept_def]
    )
    for stmt in body:
        for n in ast.walk(stmt):
            binop = isinstance(n, ast.BinOp) and not isinstance(n.op, (ast.BitOr, ast.BitAnd))
            if binop and name in names_referenced(n):
                return True
    return False


def is_cross_package_bridge(name: str, ff: FileFacts, _kept_def: ast.AST | None = None) -> bool:
    """The removed name's own definition references a name imported from a
    DIFFERENT top-level package that looks like a shared-namespace accessor.

    This "key/prefix" filter is a DELIBERATE, ADMITTED heuristic, not a clean
    rule. An earlier version fired on ANY cross-package reference and produced
    false positives on ordinary layered dependencies (e.g.
    ``registry_backfill.backfill._load_meta`` calling ``storage.get_minio()``
    -- a normal one-directional architecture dependency, not a same-feature
    bridge). A frequency cutoff does not separate the two cases either:
    ``get_minio`` and ``bridge_redis_key`` are both imported by exactly one
    file. The genuine worker/metrics bridge is a RUNTIME contract (both sides
    format the same Redis key string), which import-graph AST analysis cannot
    see at all -- this heuristic is a proxy for that, not a substitute.
    """
    if not ff.cross_pkg_imports:
        return False
    keyish = {n for n in ff.cross_pkg_imports if re.search(r"key|prefix|topic|channel", n, re.I)}
    if not keyish:
        return False
    for node in ff.tree.body:
        is_own_def = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        if is_own_def and names_referenced(node) & keyish:
            return True
    return False


#: A quoted string matching NAME that is really just an environment variable
#: of the same spelling (a common convention here:
#: ``_FOO_ENABLED = os.getenv("FOO_ENABLED", ...)``) is not a string-keyed
#: registry hit -- it does not consume the Python symbol at all, it only
#: re-declares an independently-read env var under the same name. Excluded so
#: this signal is not self-polluting on every ``_ENABLED``-style constant.
_ENV_READ_CALL = re.compile(r"\b(os\.getenv|os\.environ\.get|_envbool|getenv)\s*\(")


def string_registry_hits(name: str, own_file: pathlib.Path | None) -> list[str]:
    """Exact-quoted-string occurrences of NAME elsewhere in ``src/``, outside
    the defining file -- dict keys, literal comparisons, ``getattr`` dispatch
    targets -- excluding env-var-name coincidences."""
    hits = []
    pattern = re.compile(r'["\']' + re.escape(name) + r'["\']')
    for py in SRC.rglob("*.py"):
        if own_file and py.resolve() == own_file.resolve():
            continue
        if py.name == "__init__.py":
            continue
        try:
            text = py.read_text()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line) and not _ENV_READ_CALL.search(line):
                hits.append(f"{py.relative_to(REPO)}:{i}")
    return hits


def broad_reference(name: str, kept_def: ast.AST) -> bool:
    """Calibration only: does NAME appear anywhere, in any reference form,
    inside a kept sibling's body? Tests whether a blanket "referenced
    somewhere" rule collapses the removal set."""
    return name in names_referenced(kept_def)


# ---------------------------------------------------------------------------
# 5. Run every candidate through the signal battery.
# ---------------------------------------------------------------------------
def _blank_row(pkg: str, name: str, mod_file: pathlib.Path | None) -> dict:
    exists = bool(mod_file and mod_file.exists())
    return {
        "package": pkg,
        "name": name,
        "in_split_table": (pkg, name) in SPLIT_NAMES,
        "module_file": str(mod_file.relative_to(REPO)) if exists else None,
        "flag_gate": False,
        "type_annotation": False,
        "arithmetic_operand": False,
        "cross_package": False,
        "string_registry": [],
        "broad_reference": False,
        "broad_reference_via": None,
    }


def _scan_package(row: dict, name: str, pkg: str, kept_names: set[str], kind: str) -> None:
    """Scan every submodule in ``pkg`` for kept defs that couple to ``name``.

    Scans the whole package, not just the name's own defining file: a type can
    be defined in ``types.py`` but constructed in ``pipeline.py``, and the
    facade does not care which submodule holds the def, so neither may the
    coupling search.
    """
    for py in sorted((SRC / pkg).glob("*.py")):
        if py.name == "__init__.py":
            continue
        ff = FileFacts(py, kept_names)
        for kept_def in ff.kept_defs():
            if is_flag_gate(name, ff, kept_def):
                row["flag_gate"] = True
            if is_type_annotation(name, kept_def, is_class=(kind == "class")):
                row["type_annotation"] = True
            if is_arithmetic_operand(name, kept_def):
                row["arithmetic_operand"] = True
            if broad_reference(name, kept_def) and not row["broad_reference"]:
                row["broad_reference"] = True
                row["broad_reference_via"] = f"{py.name}:{kept_def.name}"
        if is_cross_package_bridge(name, ff):
            row["cross_package"] = True


def _apply_disposition(row: dict) -> None:
    keep_reasons = [s for s in NARROW_SIGNALS if row[s]]
    if row["string_registry"]:
        keep_reasons.append("string_registry")
    row["narrow_disposition"] = "KEEP" if keep_reasons else "REMOVE"
    row["narrow_reasons"] = keep_reasons
    row["broad_disposition"] = "KEEP" if (keep_reasons or row["broad_reference"]) else "REMOVE"


def measure(removals: list[tuple[str, str]] | None = None) -> list[dict]:
    """Run the full signal battery and return one result row per candidate."""
    removals = removals if removals is not None else load_removals()
    init_cache = {pkg: parse_init(pkg) for pkg in {p for p, _ in removals}}

    results = []
    for pkg, name in removals:
        all_names, _blocks = init_cache[pkg]
        kept_names = all_names - {n for p, n in removals if p == pkg}
        mod_file = module_file_for(pkg, name, init_cache)

        row = _blank_row(pkg, name, mod_file)
        row["definition_kind"] = definition_kind(name, mod_file)
        _scan_package(row, name, pkg, kept_names, row["definition_kind"])
        # FuturesTimeoutError's defining file is stdlib, so for that candidate
        # the coupling is entirely the string-registry channel.
        row["string_registry"] = string_registry_hits(name, mod_file)
        _apply_disposition(row)
        results.append(row)
    return results


# ---------------------------------------------------------------------------
# 6. Report.
# ---------------------------------------------------------------------------
def _print_rows(results: list[dict]) -> None:
    print("=" * 100)
    print(f"PER-CANDIDATE RESULTS ({len(results)} total)")
    print("=" * 100)
    for row in results:
        tag = "[SPLIT]" if row["in_split_table"] else "       "
        reasons = ",".join(row["narrow_reasons"]) or "-"
        broad = "Y" if row["broad_reference"] else "n"
        print(
            f"{tag} {row['package']:18} {row['name']:42} "
            f"narrow={row['narrow_disposition']:6} ({reasons:35}) "
            f"broad_ref={broad} via={row['broad_reference_via']}"
        )


def _print_calibration(results: list[dict]) -> None:
    total = len(results)
    broad_keep = sum(1 for r in results if r["broad_disposition"] == "KEEP")
    narrow_keep = sum(1 for r in results if r["narrow_disposition"] == "KEEP")
    print()
    print("=" * 100)
    print("CALIBRATION: broad 'referenced anywhere in a kept sibling' rule")
    print("=" * 100)
    print(
        f"Under BROAD rule  (any reference at all blocks removal): "
        f"{total - broad_keep} of {total} survive as REMOVE, {broad_keep} flip to KEEP"
    )
    print(
        f"Under NARROW rule (flag-gate / type-annotation / arithmetic-operand / "
        f"cross-package / string-registry): "
        f"{total - narrow_keep} of {total} survive as REMOVE, {narrow_keep} flip to KEEP"
    )


def _print_per_package(results: list[dict], removals: list[tuple[str, str]]) -> None:
    print()
    print("=" * 100)
    print("NARROW-RULE FINAL REMOVAL COUNT, PER PACKAGE")
    print("=" * 100)
    per_pkg_total = Counter(pkg for pkg, _ in removals)
    per_pkg_keep = Counter(r["package"] for r in results if r["narrow_disposition"] == "KEEP")
    narrow_keep = sum(per_pkg_keep.values())
    total_survive = 0
    for pkg in sorted(per_pkg_total):
        keep = per_pkg_keep.get(pkg, 0)
        survive = per_pkg_total[pkg] - keep
        total_survive += survive
        print(
            f"  {pkg:20} proposed={per_pkg_total[pkg]:3}  "
            f"flips_to_keep={keep:3}  final_removal={survive:3}"
        )
    print(
        f"  {'TOTAL':20} proposed={len(removals):3}  "
        f"flips_to_keep={narrow_keep:3}  final_removal={total_survive:3}"
    )


def _print_flips(results: list[dict]) -> None:
    print()
    print("=" * 100)
    print("NAMES THAT FLIP FROM REMOVE -> KEEP UNDER THE NARROW RULE, WITH CATCHING RULE")
    print("=" * 100)
    for row in results:
        if row["narrow_disposition"] != "KEEP":
            continue
        print(f"  {row['package']:18} {row['name']:42} <- {', '.join(row['narrow_reasons'])}")
        if row["string_registry"]:
            print(f"      string hits: {row['string_registry'][:4]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--json", type=pathlib.Path, default=None, help="write full per-candidate results as JSON"
    )
    parser.add_argument(
        "--manifest",
        type=pathlib.Path,
        default=MANIFEST,
        help="audit manifest to read section B from",
    )
    args = parser.parse_args(argv)

    removals = load_removals(args.manifest)
    if len(removals) != EXPECTED_CANDIDATES:
        parser.error(f"expected {EXPECTED_CANDIDATES} candidates in section B, got {len(removals)}")

    results = measure(removals)
    _print_rows(results)
    _print_calibration(results)
    _print_per_package(results, removals)
    _print_flips(results)

    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
        print(f"\nFull JSON written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
