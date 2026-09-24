"""Shared helpers for the test-budget toolkit (stdlib only).

Every script in scripts/testbudget/ imports from here. No third-party imports,
ever -- these scripts must run on a bare interpreter, before `uv sync`, and
inside a constrained cgroup.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# -- Layout -------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "tests"
SRC_DIR = REPO_ROOT / "src"
BASELINE_DIR = REPO_ROOT / "scripts" / "testbudget" / ".baseline"
TEST_INDEX = TESTS_DIR / "TEST_INDEX.yaml"
CONTRACTS_DIR = REPO_ROOT / "agents" / "contracts"


def scratch_dir() -> Path:
    """Where long output goes. Never the transcript."""
    env = os.environ.get("TESTBUDGET_SCRATCH")
    p = Path(env) if env else Path("/tmp") / "testbudget"
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_detail(name: str, payload) -> Path:
    """Dump full detail to scratch; return the path to *print*, not the content."""
    path = scratch_dir() / name
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def die(msg: str, code: int = 2):
    print("ERROR: " + msg, file=sys.stderr)
    raise SystemExit(code)


# -- Test-file discovery ------------------------------------------------------


def test_files(tests_dir=None) -> list:
    d = tests_dir or TESTS_DIR
    if not d.exists():
        die("no tests/ directory at %s" % d)
    return sorted(p for p in d.glob("test_*.py") if p.is_file())


def src_files(src_dir=None) -> list:
    d = src_dir or SRC_DIR
    if not d.exists():
        return []
    return sorted(
        p for p in d.rglob("*.py") if p.name != "__init__.py" and "__pycache__" not in p.parts
    )


# -- AST facts about one test file --------------------------------------------


@dataclass
class FileFacts:
    path: str
    defs: int = 0  # `def test_*` count (what the unit gate greps)
    params: int = 0  # @pytest.mark.parametrize decorators
    param_rows: int = 0  # total rows across all parametrize tables
    asserts: int = 0  # assert statements
    raises: int = 0  # pytest.raises / mock assert_* calls
    skips: int = 0  # unconditional skip / xfail markers
    classes: int = 0
    lines: int = 0
    collected: int = -1  # filled by pytest --collect-only; -1 = unknown
    parse_error: str = ""

    @property
    def assert_density(self) -> float:
        return round(self.asserts / self.defs, 2) if self.defs else 0.0

    @property
    def count(self) -> int:
        """Collected if known, else the AST def count."""
        return self.collected if self.collected >= 0 else self.defs


def decorator_name(dec) -> str:
    node = dec.func if isinstance(dec, ast.Call) else dec
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def parametrize_rows(dec) -> int:
    """Number of cases a @parametrize decorator contributes. 0 if not literal."""
    if not isinstance(dec, ast.Call) or len(dec.args) < 2:
        return 0
    values = dec.args[1]
    if isinstance(values, (ast.List, ast.Tuple, ast.Set)):
        return len(values.elts)
    return 0


def is_test_func(node) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
        "test_"
    )


def iter_test_funcs(tree):
    """Yield (func_node, class_name_or_None) for every test function."""
    for node in tree.body:
        if is_test_func(node):
            yield node, None
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if is_test_func(sub):
                    yield sub, node.name


_HARD_SKIP = re.compile(r"\b(skip|xfail)\b")


def unconditional_skip(dec) -> bool:
    """skip(...) always fires; skipif(<literal truthy>) always fires."""
    name = decorator_name(dec)
    if name.endswith("skipif"):
        if isinstance(dec, ast.Call) and dec.args:
            arg = dec.args[0]
            return isinstance(arg, ast.Constant) and bool(arg.value)
        return False
    return name.endswith("skip") or name.endswith("xfail")


def assert_like_call(call) -> bool:
    cname = decorator_name(call)
    return (
        cname.endswith("raises")
        or ".assert_" in cname
        or cname.startswith("assert_")
        or cname.endswith("assert_called")
        or cname.endswith("fail")
    )


def file_facts(path: Path) -> FileFacts:
    f = FileFacts(path=str(path.relative_to(REPO_ROOT)))
    text = path.read_text(errors="replace")
    f.lines = text.count("\n") + 1
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        f.parse_error = "%s: %s" % (exc.__class__.__name__, exc)
        return f
    f.classes = sum(1 for n in tree.body if isinstance(n, ast.ClassDef))
    for func, _cls in iter_test_funcs(tree):
        f.defs += 1
        for dec in func.decorator_list:
            name = decorator_name(dec)
            if name.endswith("parametrize"):
                f.params += 1
                f.param_rows += parametrize_rows(dec)
            elif _HARD_SKIP.search(name) and unconditional_skip(dec):
                f.skips += 1
        for sub in ast.walk(func):
            if isinstance(sub, ast.Assert):
                f.asserts += 1
            elif isinstance(sub, ast.Call) and assert_like_call(sub):
                f.raises += 1
    return f


# -- Collection (cheap, safe -- never runs a test body) -----------------------


def collect_counts(paths: list, timeout: int = 600) -> dict:
    """Per-file collected-test counts via `pytest --collect-only -q`.

    Collection imports test modules but executes no test body and needs no
    memory cap. It is the only subprocess any of these scripts runs.
    Returns {} if pytest is unavailable -- callers degrade to AST `defs`.
    """
    cmd = ["uv", "run", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
    cmd += [str(p) for p in paths]
    try:
        out = subprocess.run(
            cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=timeout
        )
    except Exception as exc:
        print(
            "  (collect skipped: %s) -- falling back to AST def counts" % exc.__class__.__name__,
            file=sys.stderr,
        )
        return {}
    counts = {}
    for line in out.stdout.splitlines():
        line = line.strip()
        if "::" not in line or line.startswith(("ERROR", "E ", "<")):
            continue
        fname = line.split("::", 1)[0]
        if not fname.endswith(".py"):
            continue
        counts[fname] = counts.get(fname, 0) + 1
    if not counts and out.returncode != 0:
        print(
            "  (collect failed rc=%s) -- falling back to AST def counts" % out.returncode,
            file=sys.stderr,
        )
    return counts


# -- Baseline I/O -------------------------------------------------------------


def save_baseline(payload: dict, label: str = "census") -> Path:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    path = BASELINE_DIR / ("%s-%s.json" % (label, stamp))
    blob = json.dumps(payload, indent=2, default=str)
    path.write_text(blob)
    (BASELINE_DIR / ("%s-latest.json" % label)).write_text(blob)
    return path


def load_baseline(label: str = "census", path=None) -> dict:
    p = Path(path) if path else BASELINE_DIR / ("%s-latest.json" % label)
    if not p.exists():
        die("no baseline at %s -- run scripts/testbudget/census.py first" % p)
    return json.loads(p.read_text())


# -- Small formatting helpers -------------------------------------------------


def table(rows: list, headers: list, limit=None) -> str:
    shown = rows if limit is None else rows[:limit]
    cols = [[str(h)] + [str(r[i]) for r in shown] for i, h in enumerate(headers)]
    widths = [max(len(c) for c in col) for col in cols]

    def fmt(vals):
        out = []
        for i, v in enumerate(vals):
            v = str(v)
            out.append(v.ljust(widths[i]) if i == 0 else v.rjust(widths[i]))
        return "  ".join(out).rstrip()

    lines = [fmt(headers), fmt(["-" * w for w in widths])]
    lines += [fmt(r) for r in shown]
    if limit is not None and len(rows) > limit:
        lines.append("... %d more rows in the detail file" % (len(rows) - limit))
    return "\n".join(lines)


def git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git"] + list(args), cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120
        ).stdout.strip()
    except Exception:
        return ""
