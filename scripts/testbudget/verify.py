#!/usr/bin/env python3
"""Test-budget VERIFIER -- did the cut actually hold?

Re-runs the census, diffs it against the saved baseline, and re-checks the
governance invariants a reduction is most likely to have broken:

  1  count       collected at or below target, and what actually moved
  2  coverage    vs the committed baseline (requires --coverage-xml)
  3  density     assertion density still at or above the unit-gate floor
  4  ratio       test/src FILE ratio still under the test-ratio-guard ceiling
  5  index       TEST_INDEX.yaml: every src mapped, every mapping exists,
                 no orphan test file
  6  contracts   every contract ID under agents/contracts/ still grep-findable
                 in tests/ -- searching .py ONLY, never bytecode
  7  layers      per-module test file still present for each gated layer
  8  ratchet     tests/TEST_BUDGET.baseline agrees with the new collected count

Exits non-zero if any check FAILs. Static and cheap: it runs no tests. The
full suite is a separate, capped, foreground step -- see the skill.

Usage:
  scripts/testbudget/verify.py --target 950
  scripts/testbudget/verify.py --target 950 --coverage-xml coverage.xml
  scripts/testbudget/verify.py --baseline scripts/testbudget/.baseline/census-before.json
  scripts/testbudget/verify.py --run-gates      # also exec the static gate scripts
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tb  # noqa: E402

LAYER_MODULES = ["client", "storage", "cache", "worker", "converters"]
DENSITY_FLOOR = 2.0
RATIO_CEILING = 0.65

RESULTS = []


def check(name: str, ok, msg: str, warn_only: bool = False):
    status = "PASS" if ok else ("WARN" if warn_only else "FAIL")
    RESULTS.append((status, name, msg))


def read_index() -> dict:
    """Minimal TEST_INDEX.yaml parse -- same shape the bash gate parses."""
    mapping, cur = {}, None
    if not _tb.TEST_INDEX.exists():
        return {}
    for line in _tb.TEST_INDEX.read_text().splitlines():
        m = re.match(r"^ {2}([A-Za-z0-9_/]+\.py):\s*$", line)
        if m:
            cur = m.group(1)
            mapping[cur] = []
            continue
        m = re.match(r"^ {6}-\s+(test_[A-Za-z0-9_]+\.py)", line)
        if m and cur:
            mapping[cur].append(m.group(1))
    return mapping


def contract_ids() -> list:
    ids = []
    if not _tb.CONTRACTS_DIR.exists():
        return ids
    for y in sorted(_tb.CONTRACTS_DIR.glob("*.yaml")):
        text = y.read_text(errors="replace")
        if re.search(r"^meta:\s*true", text, re.M):
            continue
        for m in re.finditer(r"^\s*-?\s*id:\s*\"?([A-Za-z0-9_.-]+)\"?\s*$", text, re.M):
            ids.append((y.name, m.group(1)))
    return ids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=int, default=None, help="collected-test target to assert")
    ap.add_argument("--baseline", default=None, help="baseline census JSON to diff against")
    ap.add_argument("--coverage-xml", default=None, help="post-cut coverage.xml")
    ap.add_argument("--no-collect", action="store_true", help="AST counts only")
    ap.add_argument("--run-gates", action="store_true",
                    help="also exec the static gate scripts (never the pytest gate)")
    args = ap.parse_args()

    base = _tb.load_baseline(path=args.baseline)
    paths = _tb.test_files()
    facts = [_tb.file_facts(p) for p in paths]
    if not args.no_collect:
        counts = _tb.collect_counts(paths)
        for f in facts:
            if f.path in counts:
                f.collected = counts[f.path]

    now_n = sum(f.count for f in facts)
    now_defs = sum(f.defs for f in facts)
    now_asserts = sum(f.asserts for f in facts)
    density = round(now_asserts / now_defs, 2) if now_defs else 0.0
    srcs = _tb.src_files()
    ratio = round(len(paths) / len(srcs), 3) if srcs else 0.0

    # 1 count
    was = base["collected"]
    delta = now_n - was
    if args.target is not None:
        check("count", now_n <= args.target,
              "collected %d vs target %d (was %d, %+d)" % (now_n, args.target, was, delta))
    else:
        check("count", True, "collected %d (was %d, %+d) -- no --target given" % (now_n, was, delta))

    # 2 coverage
    if args.coverage_xml:
        root = ElementTree.parse(args.coverage_xml).getroot()
        cov = round(float(root.attrib.get("line-rate", "0")) * 100, 2)
        bcov = base.get("coverage_pct")
        if bcov is None:
            check("coverage", False, "now %.2f%% but the baseline recorded none -- "
                  "'coverage held' is unprovable" % cov, warn_only=True)
        else:
            check("coverage", cov >= bcov - 0.5,
                  "%.2f%% vs baseline %.2f%% (%+.2f)" % (cov, bcov, cov - bcov))
    else:
        check("coverage", False, "no --coverage-xml given -- coverage UNVERIFIED", warn_only=True)

    # 3 density  4 ratio
    check("density", density >= DENSITY_FLOOR,
          "%.2f asserts/def (unit gate floor %.1f, was %.2f)"
          % (density, DENSITY_FLOOR, base.get("assert_density", 0)))
    check("ratio", ratio <= RATIO_CEILING,
          "%.3f test/src files (ceiling %.2f; %d/%d)" % (ratio, RATIO_CEILING, len(paths), len(srcs)))

    # 5 index
    idx = read_index()
    if not idx:
        check("index", False, "tests/TEST_INDEX.yaml missing or unparseable")
    else:
        listed = {t for v in idx.values() for t in v}
        on_disk = {p.name for p in paths}
        missing = sorted(listed - on_disk)
        orphan = sorted(on_disk - listed)
        unmapped = sorted(str(s.relative_to(_tb.SRC_DIR / "pageindex_mcp"))
                          for s in srcs if str(s.relative_to(_tb.SRC_DIR / "pageindex_mcp")) not in idx)
        problems = []
        if missing:
            problems.append("%d mapping(s) point at deleted files: %s" % (len(missing), ", ".join(missing[:4])))
        if orphan:
            problems.append("%d unmapped test file(s): %s" % (len(orphan), ", ".join(orphan[:4])))
        if unmapped:
            problems.append("%d src file(s) with no entry: %s" % (len(unmapped), ", ".join(unmapped[:4])))
        check("index", not problems, "; ".join(problems) or
              "%d src entries, %d test files, no orphans" % (len(idx), len(listed)))

    # 6 contracts -- .py only. A stale .pyc under tests/__pycache__ will happily
    #    satisfy a naive `grep -r`, masking a contract test that was deleted.
    ids = contract_ids()
    if not ids:
        check("contracts", True, "no non-meta contract IDs found")
    else:
        blob = "\n".join(p.read_text(errors="replace") for p in _tb.TESTS_DIR.rglob("*.py"))
        missing = [cid for _y, cid in ids if cid not in blob]
        check("contracts", not missing,
              "%d/%d IDs findable in tests/**.py%s"
              % (len(ids) - len(missing), len(ids),
                 "; MISSING: " + ", ".join(missing[:6]) if missing else ""))

    # 7 layers
    bad = [m for m in LAYER_MODULES
           if (_tb.SRC_DIR / "pageindex_mcp" / ("%s.py" % m)).exists()
           or (_tb.SRC_DIR / "pageindex_mcp" / m).is_dir()]
    bad = [m for m in bad
           if not (_tb.TESTS_DIR / ("test_%s.py" % m)).exists()
           and not (_tb.TESTS_DIR / ("test_%s_contract.py" % m)).exists()]
    check("layers", not bad, "missing per-module file for: %s" % ", ".join(bad) if bad
          else "every gated layer has test_<module>.py or _contract.py")

    # 8 ratchet -- the committed collected-count budget (scripts/gates/test_budget.sh)
    ratchet = _tb.REPO_ROOT / "tests" / "TEST_BUDGET.baseline"
    if not ratchet.exists():
        check("ratchet", True, "no tests/TEST_BUDGET.baseline in this repo", warn_only=True)
    else:
        num = ""
        for line in ratchet.read_text().splitlines():
            t = line.strip()
            if t and not t.startswith("#"):
                num = "".join(c for c in t if c.isdigit())
                break
        if not num:
            check("ratchet", False, "TEST_BUDGET.baseline holds no integer")
        else:
            b, allowance = int(num), 15
            check("ratchet", abs(now_n - b) <= allowance,
                  "collected %d vs ratchet %d (allowance +/-%d) -- lower it in the "
                  "SAME commit as the cut" % (now_n, b, allowance))

    print("== VERIFY  baseline %s (%s)" % (base.get("commit", "?")[:9], base.get("branch", "?")))
    print(_tb.table([[s, n, m] for s, n, m in RESULTS], ["", "CHECK", "DETAIL"]))
    fails = [r for r in RESULTS if r[0] == "FAIL"]
    warns = [r for r in RESULTS if r[0] == "WARN"]
    print()

    if args.run_gates:
        print("-- static gates")
        for g in ("contracts.sh", "test-index-guard.sh --full", "test-ratio-guard.sh"):
            cmd = [str(_tb.REPO_ROOT / "scripts" / "gates" / g.split()[0])] + g.split()[1:]
            try:
                r = subprocess.run(cmd, cwd=str(_tb.REPO_ROOT), capture_output=True,
                                   text=True, timeout=600)
                tail = [l for l in r.stdout.splitlines() if l.startswith(("PASS gate", "FAIL gate", "SKIP gate"))]
                print("  %-28s %s" % (g, tail[-1] if tail else "rc=%d" % r.returncode))
                if r.returncode != 0:
                    fails.append(("FAIL", "gate:" + g, "rc=%d" % r.returncode))
            except Exception as exc:
                print("  %-28s could not run: %s" % (g, exc))
        print()

    print("VERDICT: %s  (%d fail, %d warn, %d pass)"
          % ("FAIL" if fails else "PASS", len(fails), len(warns),
             len(RESULTS) - len(fails) - len(warns)))
    print("Still owed, and NOT checked here: one capped foreground suite run --")
    print("  make test TEST_MEM_MAX=900M PYTEST_ARGS=\"-q\"")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
