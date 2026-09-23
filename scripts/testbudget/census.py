#!/usr/bin/env python3
"""Test-budget CENSUS -- measure the suite before touching anything.

Emits one compact ranked table plus the global totals, and writes a baseline
JSON that `verify.py` later diffs against. Without that baseline, "coverage
held" and "we cut N tests" are assertions, not facts.

Cost: one `pytest --collect-only` (imports modules, runs no test body, needs
no memory cap). Everything else is stdlib AST. Nothing here ever runs a test.

Usage:
  scripts/testbudget/census.py                     # collect + AST census
  scripts/testbudget/census.py --no-collect        # AST only (no subprocess)
  scripts/testbudget/census.py --coverage-xml coverage.xml --runtime 504
  scripts/testbudget/census.py --archaeology       # + git growth vs merge-base
  scripts/testbudget/census.py --label after       # save under a second label
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tb  # noqa: E402


def coverage_pct(xml_path: Path) -> float:
    root = ElementTree.parse(str(xml_path)).getroot()
    return round(float(root.attrib.get("line-rate", "0")) * 100, 2)


def archaeology(files: list) -> dict:
    """WHEN did the suite grow, and WHICH commits did it?"""
    base = _tb.git("merge-base", "HEAD", "master") or _tb.git("merge-base", "HEAD", "main")
    out = {"merge_base": base, "per_commit": [], "then": None, "now": None}
    if not base:
        return out
    def defs_at(rev: str) -> int:
        blob = _tb.git("grep", "-h", "-c", "-E", r"^\s*(async )?def test_", rev, "--", "tests/")
        return sum(int(x) for x in blob.split() if x.isdigit())
    out["then"] = defs_at(base)
    out["now"] = sum(f.defs for f in files)
    log = _tb.git("log", "--format=%h|%ad|%s", "--date=short", "%s..HEAD" % base, "--", "tests/")
    for line in log.splitlines():
        h = line.split("|", 1)[0]
        stat = _tb.git("show", "--numstat", "--format=", h, "--", "tests/")
        added = sum(int(l.split("\t")[0]) for l in stat.splitlines()
                    if l.split("\t")[0].isdigit())
        removed = sum(int(l.split("\t")[1]) for l in stat.splitlines()
                      if len(l.split("\t")) > 1 and l.split("\t")[1].isdigit())
        out["per_commit"].append({"commit": line, "lines_added": added,
                                  "lines_removed": removed, "net": added - removed})
    out["per_commit"].sort(key=lambda c: -c["net"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tests-dir", default=None, help="default: <repo>/tests")
    ap.add_argument("--no-collect", action="store_true",
                    help="skip pytest --collect-only; use AST def counts")
    ap.add_argument("--coverage-xml", default=None,
                    help="record total line coverage from an existing coverage.xml")
    ap.add_argument("--runtime", type=float, default=None,
                    help="wall-clock seconds of the last full capped run, for the record")
    ap.add_argument("--peak-rss-mb", type=float, default=None,
                    help="peak RSS MB of that run, for the record")
    ap.add_argument("--archaeology", action="store_true",
                    help="also diff test growth against the merge-base")
    ap.add_argument("--label", default="census", help="baseline label (default: census)")
    ap.add_argument("--top", type=int, default=15, help="rows to print (default 15)")
    args = ap.parse_args()

    tests_dir = Path(args.tests_dir) if args.tests_dir else _tb.TESTS_DIR
    paths = _tb.test_files(tests_dir)
    if not paths:
        _tb.die("no test_*.py files under %s" % tests_dir)

    facts = [_tb.file_facts(p) for p in paths]
    broken = [f for f in facts if f.parse_error]

    if not args.no_collect:
        counts = _tb.collect_counts(paths)
        for f in facts:
            if f.path in counts:
                f.collected = counts[f.path]

    srcs = _tb.src_files()
    tot_collected = sum(f.count for f in facts)
    tot_defs = sum(f.defs for f in facts)
    tot_asserts = sum(f.asserts for f in facts)
    ratio = round(len(paths) / len(srcs), 3) if srcs else 0.0
    density = round(tot_asserts / tot_defs, 2) if tot_defs else 0.0

    rows = sorted(facts, key=lambda f: -f.count)
    tbl = [[f.path.replace("tests/", ""), f.count, f.defs, f.params,
            f.param_rows, f.asserts, f.assert_density, f.skips] for f in rows]

    print("== TEST BUDGET CENSUS  %s  (%s)" % (time.strftime("%Y-%m-%dT%H:%M:%S"),
                                               _tb.git("rev-parse", "--short", "HEAD") or "no-git"))
    print("files=%d  collected=%d  def_test=%d  parametrize_decorators=%d  param_rows=%d"
          % (len(paths), tot_collected, tot_defs, sum(f.params for f in facts),
             sum(f.param_rows for f in facts)))
    print("asserts=%d  assert_density=%.2f/def (unit gate floor 2.0)  hard_skips=%d"
          % (tot_asserts, density, sum(f.skips for f in facts)))
    print("src_files=%d  test/src file ratio=%.3f (test-ratio-guard ceiling 0.65)"
          % (len(srcs), ratio))
    if args.coverage_xml:
        cov = coverage_pct(Path(args.coverage_xml))
        print("coverage=%.2f%%  (from %s)" % (cov, args.coverage_xml))
    else:
        cov = None
        print("coverage=UNRECORDED  -- rerun with --coverage-xml coverage.xml "
              "after one capped `make test` or the suite has no comparable baseline")
    if args.runtime:
        print("runtime=%.0fs  peak_rss=%s" % (args.runtime, args.peak_rss_mb or "n/a"))
    if broken:
        print("PARSE ERRORS in %d file(s): %s"
              % (len(broken), ", ".join(f.path for f in broken[:5])))
    print()
    print(_tb.table(tbl, ["FILE", "COLL", "DEFS", "PARAM", "ROWS", "ASRT", "A/DEF", "SKIP"],
                    limit=args.top))
    print()

    payload = {
        "timestamp": time.time(),
        "commit": _tb.git("rev-parse", "HEAD"),
        "branch": _tb.git("rev-parse", "--abbrev-ref", "HEAD"),
        "files": len(paths), "collected": tot_collected, "defs": tot_defs,
        "asserts": tot_asserts, "assert_density": density,
        "param_decorators": sum(f.params for f in facts),
        "param_rows": sum(f.param_rows for f in facts),
        "hard_skips": sum(f.skips for f in facts),
        "src_files": len(srcs), "test_src_ratio": ratio,
        "coverage_pct": cov, "runtime_s": args.runtime, "peak_rss_mb": args.peak_rss_mb,
        "collected_is_estimate": args.no_collect or all(f.collected < 0 for f in facts),
        "per_file": [f.__dict__ | {"assert_density": f.assert_density} for f in facts],
    }
    if args.archaeology:
        arch = archaeology(facts)
        payload["archaeology"] = arch
        if arch["merge_base"]:
            then, now = arch["then"] or 0, arch["now"]
            pct = ("%+.0f%%" % ((now - then) / then * 100)) if then else "n/a"
            print("-- GIT ARCHAEOLOGY vs merge-base %s: def_test %d -> %d (%s)"
                  % (arch["merge_base"][:9], then, now, pct))
            top = [[c["commit"][:72], c["lines_added"], c["lines_removed"], c["net"]]
                   for c in arch["per_commit"][:8]]
            if top:
                print(_tb.table(top, ["COMMIT (tests/ only)", "+", "-", "NET"]))
            print()
        else:
            print("-- GIT ARCHAEOLOGY skipped: no merge-base with master/main\n")

    bpath = _tb.save_baseline(payload, args.label)
    dpath = _tb.write_detail("census-per-file.json", payload["per_file"])
    print("baseline: %s  (and %s-latest.json)" % (bpath, args.label))
    print("detail:   %s" % dpath)
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
