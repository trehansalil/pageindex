#!/usr/bin/env python3
"""Test-budget CANDIDATE SCAN -- find the cheap mass, ranked by saving.

Four scans, in descending yield:
  A  parametrize tables   -- an N-row table driving one assertion collapses to 1
  B  tautologies          -- five shapes of test that cannot fail
  C  dead collection      -- unconditional skip / xfail
  D  RFC-wave files       -- files named for an RFC/wave/zone that re-test an
                             invariant a topical home already owns

Pure stdlib AST. Runs no tests, starts no subprocess, reads no file twice.
Prints a ranked summary only; full per-test detail goes to a scratch JSON.

EVERY HIT IS A CANDIDATE, NOT A VERDICT. Shapes B2/B5 and all of D need a
human read before deletion -- see the skill's "traps" section.

Usage:
  scripts/testbudget/scan_candidates.py                  # all scans
  scripts/testbudget/scan_candidates.py --scan A,B
  scripts/testbudget/scan_candidates.py --files tests/test_verdict.py ...
  scripts/testbudget/scan_candidates.py --top 25
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tb  # noqa: E402

RFC_FILE_RE = re.compile(r"^test_(rfc\d+|wave\d*|zone\d*|hr\d+|d\d+)\b|_(rfc\d+|wave\d+|zone\d+)\b", re.I)
RFC_BODY_RE = re.compile(r"\bRFC-?\d{2,3}\b")


def local_modules() -> set:
    """Top-level importable names that belong to THIS repo, not the stdlib."""
    names = set()
    for p in _tb.REPO_ROOT.glob("*.py"):
        names.add(p.stem)
    for d in (_tb.SRC_DIR, _tb.REPO_ROOT):
        if d.exists():
            for p in d.iterdir():
                if p.is_dir() and (p / "__init__.py").exists():
                    names.add(p.name)
    return names


LOCAL_MODULES = local_modules()


# -- helpers ------------------------------------------------------------------

def root_name(node) -> str:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def call_root(call) -> str:
    return root_name(call.func)


def _is_local(mod: str) -> bool:
    return bool(mod) and mod.split(".")[0].lstrip(".") in LOCAL_MODULES


def prod_imports(tree) -> set:
    """Names imported from this repo's own packages -- module level or inside a test."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level or _is_local(node.module or ""):
                names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if _is_local(a.name):
                    names.add((a.asname or a.name).split(".")[0])
    return names


def assert_helpers(tree) -> set:
    """Module-level helpers that assert on the caller's behalf (`_report(...)`).

    Without this, every test that delegates its checking to a helper reads as
    B4-no-assert. That was the single biggest false-positive source in the
    first version of this scanner.
    """
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("test_"):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assert) or (
                    isinstance(sub, ast.Raise) and "Assertion" in ast.dump(sub)
                ):
                    out.add(node.name)
                    break
    return out


def asserts_of(func) -> list:
    return [n for n in ast.walk(func) if isinstance(n, ast.Assert)]


def guarded_asserts(func) -> bool:
    """True when EVERY assert sits under an `if <truthy-name>:` that may be empty."""
    found = False

    def walk(node, guarded):
        nonlocal found
        ok = True
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Assert):
                found = True
                if not guarded:
                    ok = False
            elif isinstance(child, ast.If):
                weak = not isinstance(child.test, (ast.Compare, ast.Constant))
                for sub in child.body:
                    ok = walk(sub, guarded or weak) and ok
                for sub in child.orelse:
                    ok = walk(sub, guarded) and ok
                continue
            else:
                ok = walk(child, guarded) and ok
        return ok

    all_guarded = walk(func, False)
    return found and all_guarded


def recomputed_expected(func) -> bool:
    """The test recomputes the expected value with the same callable it asserts on."""
    assigned = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and isinstance(node.value, ast.Call):
                assigned[t.id] = node.value
    for a in asserts_of(func):
        t = a.test
        if not (isinstance(t, ast.Compare) and len(t.comparators) == 1):
            continue
        left, right = t.left, t.comparators[0]
        if ast.dump(left) == ast.dump(right):
            return True                              # assert x == x
        def call_of(side):
            if isinstance(side, ast.Call):
                return side
            if isinstance(side, ast.Name):
                return assigned.get(side.id)
            return None
        lc, rc = call_of(left), call_of(right)
        if lc is None or rc is None:
            continue
        if call_root(lc) != call_root(rc) or not call_root(lc):
            continue
        # A no-argument call on both sides is the before/after snapshot idiom
        # (`before = _counter(); ...; assert _counter() == before`) -- a real
        # test of a side effect, not a tautology. Only identical ARGUMENTS
        # mean the test recomputed its own expectation.
        if not lc.args and not lc.keywords:
            continue
        if ast.dump(ast.Tuple(elts=lc.args, ctx=ast.Load())) == \
           ast.dump(ast.Tuple(elts=rc.args, ctx=ast.Load())):
            return True
    return False


def isinstance_only(func) -> bool:
    ass = asserts_of(func)
    if not ass:
        return False
    for n in ast.walk(func):
        if isinstance(n, ast.Call) and _tb.assert_like_call(n):
            return False      # a mock assertion carries the real check
    for a in ass:
        t = a.test
        if isinstance(t, ast.UnaryOp):
            t = t.operand
        if not (isinstance(t, ast.Call) and call_root(t) == "isinstance"):
            return False
    return True


def no_assertion(func, helpers: set) -> bool:
    if asserts_of(func):
        return False
    for n in ast.walk(func):
        if isinstance(n, ast.Call):
            if _tb.assert_like_call(n) or call_root(n) in helpers:
                return False
    return True


def no_production_call(func, prod: set) -> bool:
    """A no-fixture test that never calls anything imported from the package."""
    if not prod:
        return False
    a = func.args
    if a.args or a.posonlyargs or a.kwonlyargs or a.vararg or a.kwarg:
        return False                                  # takes fixtures -- can't tell
    for n in ast.walk(func):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            return False                              # imports its subject locally
        if isinstance(n, ast.Call) and call_root(n) in prod:
            return False
        if isinstance(n, ast.Name) and n.id in prod:
            return False
    return True


SHAPES = [
    ("B1-isinstance-only", "only asserts the return type the function declares"),
    ("B2-recomputed", "recomputes the expected value with the code under test"),
    ("B3-guarded", "every assert sits behind an `if <x>:` that may always be empty"),
    ("B4-no-assert", "no assertion at all (a 'must not raise' smoke test may be real)"),
    ("B5-no-prod-call", "never calls anything imported from the package"),
]


# -- scans --------------------------------------------------------------------

def scan_file(path: Path, prod_only: bool = True) -> dict:
    rel = str(path.relative_to(_tb.REPO_ROOT))
    text = path.read_text(errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return {"file": rel, "error": str(exc), "params": [], "tauts": [],
                "skips": [], "collected": 0}
    prod = prod_imports(tree)
    helpers = assert_helpers(tree)
    params, tauts, skips = [], [], []
    n = 0
    for func, cls in _tb.iter_test_funcs(tree):
        n += 1
        qual = ("%s::%s" % (cls, func.name)) if cls else func.name
        rows = 1
        ptables = 0
        for dec in func.decorator_list:
            dn = _tb.decorator_name(dec)
            if dn.endswith("parametrize"):
                r = _tb.parametrize_rows(dec)
                if r:
                    rows *= r
                    ptables += 1
            elif _tb.unconditional_skip(dec):
                skips.append({"file": rel, "test": qual, "marker": dn, "line": func.lineno})
        na = len(asserts_of(func))
        if ptables and rows > 1:
            params.append({"file": rel, "test": qual, "line": func.lineno,
                           "rows": rows, "tables": ptables, "asserts": na,
                           "rows_per_assert": round(rows / na, 2) if na else rows,
                           "saving": rows - 1})
        shapes = []
        if isinstance_only(func):
            shapes.append("B1-isinstance-only")
        if recomputed_expected(func):
            shapes.append("B2-recomputed")
        if guarded_asserts(func):
            shapes.append("B3-guarded")
        if no_assertion(func, helpers):
            shapes.append("B4-no-assert")
        if no_production_call(func, prod):
            shapes.append("B5-no-prod-call")
        if shapes:
            tauts.append({"file": rel, "test": qual, "line": func.lineno,
                          "shapes": shapes, "rows": rows, "saving": rows})
    return {"file": rel, "params": params, "tauts": tauts, "skips": skips,
            "collected": n, "prod_imports": sorted(prod)}


def rfc_wave_files(paths: list, per_file: dict) -> list:
    """Files that look like a wave landed its own file instead of extending a home.

    Two signals, both structural. Merely MENTIONING an RFC id is not one of
    them -- in an RFC-driven repo almost every file does, and an earlier
    version of this scan flagged 14 of 27 files on that basis alone.
      1. the filename itself is an RFC/wave/zone/HR label
      2. the stem extends another test file's stem (test_registry_backfill.py
         against test_registry.py) -- a split of one topic across two files
    """
    stems = {p.stem: p for p in paths}
    out = []
    for p in paths:
        rel = str(p.relative_to(_tb.REPO_ROOT))
        head = "\n".join(p.read_text(errors="replace").splitlines()[:40])
        rfc_ids = sorted(set(RFC_BODY_RE.findall(head)))
        by_name = bool(RFC_FILE_RE.search(p.stem))
        home = ""
        for other in stems:
            if other != p.stem and p.stem.startswith(other + "_"):
                home = other + ".py"
                break
        if not (by_name or home):
            continue
        out.append({"file": rel, "signal": "name" if by_name else "stem-extends",
                    "rfc_ids": rfc_ids[:4], "collected": per_file.get(rel, 0),
                    "suspected_home": home})
    out.sort(key=lambda r: -r["collected"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", nargs="*", default=None, help="limit to these test files")
    ap.add_argument("--scan", default="A,B,C,D", help="subset of A,B,C,D (default all)")
    ap.add_argument("--top", type=int, default=12, help="rows per table (default 12)")
    ap.add_argument("--tests-dir", default=None)
    args = ap.parse_args()

    scans = {s.strip().upper() for s in args.scan.split(",") if s.strip()}
    if args.files:
        paths = [Path(f) if Path(f).is_absolute() else _tb.REPO_ROOT / f for f in args.files]
        missing = [p for p in paths if not p.exists()]
        if missing:
            _tb.die("no such file: %s" % missing[0])
    else:
        paths = _tb.test_files(Path(args.tests_dir) if args.tests_dir else None)

    results = [scan_file(p) for p in paths]
    per_file_n = {r["file"]: r["collected"] for r in results}
    params = [x for r in results for x in r["params"]]
    tauts = [x for r in results for x in r["tauts"]]
    skips = [x for r in results for x in r["skips"]]
    errors = [r for r in results if r.get("error")]

    params.sort(key=lambda x: (-x["saving"], -x["rows_per_assert"]))
    tauts.sort(key=lambda x: (-x["saving"], x["file"]))

    print("== CANDIDATE SCAN  files=%d  tests=%d" % (len(paths), sum(per_file_n.values())))
    if errors:
        print("PARSE ERRORS: %s" % ", ".join(e["file"] for e in errors))

    save_a = sum(x["saving"] for x in params)
    save_b = sum(x["saving"] for x in tauts)
    save_c = len(skips)
    print("A parametrize collapse : %4d tests reclaimable across %d tables"
          % (save_a, len(params)))
    print("B tautologies          : %4d tests in %d shapes (READ BEFORE DELETING)"
          % (save_b, len({s for x in tauts for s in x["shapes"]})))
    print("C dead collection      : %4d permanently skipped/xfail" % save_c)
    print()

    if "A" in scans and params:
        rows = [[x["file"].replace("tests/", "") + "::" + x["test"][:38],
                 x["rows"], x["asserts"], x["rows_per_assert"], x["saving"]]
                for x in params]
        print("-- A. PARAMETRIZE TABLES (rank: saving, then rows-per-assert)")
        print(_tb.table(rows, ["TEST", "ROWS", "ASRT", "R/A", "SAVE"], limit=args.top))
        print()

    if "B" in scans:
        byshape = {}
        for x in tauts:
            for s in x["shapes"]:
                byshape.setdefault(s, []).append(x)
        rows = [[code, len(byshape.get(code, [])), desc] for code, desc in SHAPES]
        print("-- B. TAUTOLOGY SHAPES (a hit is a candidate, not a verdict)")
        print(_tb.table(rows, ["SHAPE", "N", "WHY IT CANNOT FAIL"]))
        byfile = {}
        for x in tauts:
            byfile[x["file"]] = byfile.get(x["file"], 0) + 1
        frows = sorted(([f.replace("tests/", ""), n] for f, n in byfile.items()),
                       key=lambda r: -r[1])
        if frows:
            print()
            print(_tb.table(frows, ["FILE", "HITS"], limit=args.top))
        print()

    if "C" in scans and skips:
        rows = [[x["file"].replace("tests/", "") + "::" + x["test"][:38], x["marker"],
                 x["line"]] for x in skips]
        print("-- C. PERMANENTLY SKIPPED / XFAIL (dead code with a collection cost)")
        print(_tb.table(rows, ["TEST", "MARKER", "LINE"], limit=args.top))
        print()

    rfc = []
    if "D" in scans:
        rfc = rfc_wave_files(paths, per_file_n)
        print("-- D. SUSPECTED RFC-WAVE FILES (fold into the topical home -- human read)")
        if rfc:
            rows = [[x["file"].replace("tests/", ""), x["collected"],
                     x["signal"], x["suspected_home"] or "?"]
                    for x in rfc]
            print(_tb.table(rows, ["FILE", "COLL", "SIGNAL", "SUSPECTED HOME"], limit=args.top))
        else:
            print("  none -- no test file is named for an RFC/wave/zone")
        print()

    print("ESTIMATED CEILING: %d tests removable without touching coverage-bearing "
          "asserts\n  (A %d + B %d + C %d; D adds %d more if the folds check out)"
          % (save_a + save_b + save_c, save_a, save_b, save_c,
             sum(x["collected"] for x in rfc)))
    detail = _tb.write_detail("scan-candidates.json",
                              {"params": params, "tauts": tauts, "skips": skips,
                               "rfc_wave": rfc, "errors": errors})
    print("detail: %s" % detail)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
