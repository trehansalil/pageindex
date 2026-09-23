#!/usr/bin/env python3
"""Test-budget CLUSTER PLANNER -- disjoint file clusters, one owner each.

Parallelism is only safe when no two agents can touch the same file. This
partitions tests/ into DISJOINT clusters, keeps topically-joined files in the
same cluster (test_registry_backfill.py rides with test_registry.py), derives a
per-cluster target from the global target, and flags any cluster whose target
cannot be met from scan candidates alone -- that cluster would have to delete
coverage-bearing tests, which is the one thing an agent must never do silently.

Prints the proposed split for the user to approve or amend. Writes the
dispatch JSON (one block per cluster) to scratch.

Usage:
  scripts/testbudget/plan_clusters.py --target 950 --agents 5
  scripts/testbudget/plan_clusters.py --target 950 --agents 5 --exclude test_staging_e2e.py
  scripts/testbudget/plan_clusters.py --target-pct 40 --agents 4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tb  # noqa: E402
import scan_candidates as sc  # noqa: E402


def affinity_groups(paths: list) -> list:
    """Group files that share a topic. A file whose stem extends another's
    (test_registry_backfill -> test_registry) belongs with it."""
    stems = sorted(p.stem for p in paths)
    parent = {s: s for s in stems}
    for s in stems:
        for other in stems:
            if other != s and s.startswith(other + "_"):
                parent[s] = other
                break
    groups = {}
    by_stem = {p.stem: p for p in paths}
    for s in stems:
        root = parent[s]
        while parent[root] != root:
            root = parent[root]
        groups.setdefault(root, []).append(by_stem[s])
    return [sorted(v, key=lambda p: p.stem) for v in groups.values()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--target", type=int, help="global collected-test target")
    g.add_argument("--target-pct", type=float, help="cut this %% of the current count")
    ap.add_argument("--agents", type=int, default=5, help="number of clusters (default 5)")
    ap.add_argument("--exclude", nargs="*", default=[],
                    help="test files no agent may touch (e2e, staging, golden)")
    ap.add_argument("--baseline", default=None, help="census JSON (default: census-latest.json)")
    ap.add_argument("--tests-dir", default=None)
    args = ap.parse_args()

    if args.agents < 1:
        _tb.die("--agents must be >= 1")

    paths = [p for p in _tb.test_files(Path(args.tests_dir) if args.tests_dir else None)
             if p.name not in args.exclude]
    if not paths:
        _tb.die("every test file was excluded")

    # Counts: prefer the committed baseline (collected), fall back to AST.
    counts = {}
    try:
        base = _tb.load_baseline(path=args.baseline)
        counts = {f["path"]: (f["collected"] if f["collected"] >= 0 else f["defs"])
                  for f in base["per_file"]}
        src = "baseline %s" % base.get("commit", "")[:9]
    except SystemExit:
        src = "AST (no baseline)"
    for p in paths:
        rel = str(p.relative_to(_tb.REPO_ROOT))
        if rel not in counts:
            counts[rel] = _tb.file_facts(p).defs

    # Candidate headroom per file, from the same scan the agents will be given.
    head = {}
    for p in paths:
        r = sc.scan_file(p)
        head[r["file"]] = (sum(x["saving"] for x in r["params"])
                           + sum(x["saving"] for x in r["tauts"]) + len(r["skips"]))

    total = sum(counts[str(p.relative_to(_tb.REPO_ROOT))] for p in paths)
    target = args.target if args.target is not None else int(round(total * (1 - args.target_pct / 100)))
    if target >= total:
        _tb.die("target %d is not below the current count %d" % (target, total))
    cut = total - target

    groups = affinity_groups(paths)
    sized = sorted(
        ((g, sum(counts[str(p.relative_to(_tb.REPO_ROOT))] for p in g)) for g in groups),
        key=lambda t: -t[1])

    # Longest-processing-time bin packing: balanced, disjoint, deterministic.
    clusters = [{"files": [], "now": 0} for _ in range(args.agents)]
    for grp, size in sized:
        c = min(clusters, key=lambda c: c["now"])
        c["files"].extend(grp)
        c["now"] += size

    rows, blocks, infeasible = [], [], 0
    for i, c in enumerate(clusters, 1):
        if not c["files"]:
            continue
        rels = [str(p.relative_to(_tb.REPO_ROOT)) for p in c["files"]]
        share = c["now"] / total if total else 0
        c_cut = int(round(cut * share))
        c_target = c["now"] - c_cut
        c_head = sum(head.get(r, 0) for r in rels)
        ok = "yes" if c_head >= c_cut else "SHORT %d" % (c_cut - c_head)
        if c_head < c_cut:
            infeasible += 1
        name = "C%d-%s" % (i, c["files"][0].stem.replace("test_", ""))
        rows.append([name, len(rels), c["now"], c_target, c_cut, c_head, ok])
        blocks.append({"cluster": name, "files": rels, "now": c["now"],
                       "target": c_target, "cut": c_cut, "candidate_headroom": c_head,
                       "feasible_from_candidates": c_head >= c_cut})

    print("== CLUSTER PLAN  counts from %s" % src)
    print("files=%d  now=%d  target=%d  cut=%d (%.0f%%)  agents=%d  excluded=%s"
          % (len(paths), total, target, cut, cut / total * 100, args.agents,
             ",".join(args.exclude) or "none"))
    print()
    print(_tb.table(rows, ["CLUSTER", "FILES", "NOW", "TARGET", "CUT", "HEADROOM", "FEASIBLE"]))
    print()
    if infeasible:
        print("%d cluster(s) cannot reach their target from scan candidates alone."
              % infeasible)
        print("That is a DECISION, not a bug: accept the shortfall, re-scope the")
        print("target, or move a file between clusters. An agent must never close")
        print("the gap by deleting the last test covering a production branch.")
    else:
        print("Every cluster's target is inside its candidate headroom.")
    print()
    for r in rows:
        print("  %-22s %s" % (r[0], " ".join(
            f.replace("tests/", "") for f in
            next(b["files"] for b in blocks if b["cluster"] == r[0]))))
    print()
    detail = _tb.write_detail("cluster-plan.json",
                              {"total": total, "target": target, "cut": cut,
                               "excluded": args.exclude, "clusters": blocks})
    print("dispatch blocks: %s" % detail)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
