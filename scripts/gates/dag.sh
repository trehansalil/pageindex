#!/usr/bin/env bash
# scripts/gates/dag.sh — Gate 4: DAG Topology & Execution-Log Integrity
#
# Enforces:
#   - acyclic: the dependency graph in dag.yaml has no cycles.
#   - nodes_resolve_to_artifacts: every dag node with a check.paths block points
#     to real files/directories on disk, honoring type: file|dir|glob (+min_matches
#     for glob). Legacy singular check.path is still accepted. (kind: filesystem /
#     skill_generated nodes whose check passes count as satisfied.)
#   - execution_order_matches_topology: execution-log.jsonl entries do not
#     violate declared depends_on edges (an ancestor not in the log whose
#     check passes on disk is treated as satisfied — §4.3).
#   - derived_groups_consistent: warns when derived.parallel_group values are
#     stale. A plain run is READ-ONLY; pass --write to regenerate the derived:
#     block (a marker-splice that preserves every hand-authored comment above the
#     "# ── DERIVED FIELDS" marker). CI runs without --write so it never mutates
#     a tracked file.
#
# Needs infra: no
# Reads thresholds from .agents/governance/verify-gates.yaml via read-yaml.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIB_DIR="$REPO_ROOT/scripts/lib"
GATES_YAML="$REPO_ROOT/.agents/governance/verify-gates.yaml"
DAG_YAML="$REPO_ROOT/.agents/governance/dag.yaml"
EXEC_LOG="$REPO_ROOT/.agents/state/execution-log.jsonl"

# shellcheck source=../lib/read-yaml.sh
source "$LIB_DIR/read-yaml.sh"

PASS=0
FAIL=0
WARN=0
MESSAGES=()

pass()  { PASS=$((PASS+1));  MESSAGES+=("  [PASS]  $*"); }
fail()  { FAIL=$((FAIL+1));  MESSAGES+=("  [FAIL]  $*"); }
warn()  { WARN=$((WARN+1));  MESSAGES+=("  [WARN]  $*"); }

# ── Args: --write regenerates the derived: block (default is read-only) ────────
WRITE_MODE="false"
for arg in "$@"; do
    case "$arg" in
        --write) WRITE_MODE="true" ;;
    esac
done
GENERATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "=== Gate 4: dag ==="

cd "$REPO_ROOT"

# ── Read thresholds ────────────────────────────────────────────────────────────
ACYCLIC=$(gate_threshold              "dag.acyclic"                        2>/dev/null || echo "true")
NODES_RESOLVE=$(gate_threshold        "dag.nodes_resolve_to_artifacts"      2>/dev/null || echo "true")
EXEC_ORDER=$(gate_threshold           "dag.execution_order_matches_topology" 2>/dev/null || echo "true")
DERIVED_CONSISTENT=$(gate_threshold   "dag.derived_groups_consistent"        2>/dev/null || echo "true")

# ── Prerequisite: dag.yaml present ───────────────────────────────────────────
if [[ ! -f "$DAG_YAML" ]]; then
    echo "  [FAIL]  dag.yaml not found at $DAG_YAML"
    echo "FAIL gate=dag"
    exit 1
fi

# ── Prerequisite: Python + PyYAML available ───────────────────────────────────
PYTHON_CMD="python3"
if ! $PYTHON_CMD -c "import yaml" &>/dev/null; then
    if uv run python3 -c "import yaml" &>/dev/null 2>&1; then
        PYTHON_CMD="uv run python3"
    else
        echo "  [SKIP]  PyYAML not available; run 'uv sync --extra dev'"
        echo "SKIP gate=dag"
        exit 0
    fi
fi

# ── Python helper: parse dag.yaml and run all checks ──────────────────────────
DAG_RESULT=$($PYTHON_CMD - "$DAG_YAML" "$EXEC_LOG" "$REPO_ROOT" "$WRITE_MODE" "$GENERATED_AT" <<'PYEOF'
import sys, json, yaml, pathlib, re
from collections import defaultdict, deque

dag_path    = pathlib.Path(sys.argv[1])
log_path    = pathlib.Path(sys.argv[2])
repo_root   = pathlib.Path(sys.argv[3])
write_mode   = (len(sys.argv) > 4 and sys.argv[4] == "true")
generated_at = sys.argv[5] if len(sys.argv) > 5 else ""

with open(dag_path) as f:
    dag = yaml.safe_load(f)

issues     = []   # (severity, message)
warnings   = []

# ── Flatten all nodes across all sections ─────────────────────────────────────
all_nodes  = {}   # id -> node dict

def collect_nodes(section):
    if isinstance(section, dict):
        for node in section.get("nodes", []):
            nid = node.get("id")
            if nid:
                all_nodes[nid] = node
    elif isinstance(section, list):
        for node in section:
            nid = node.get("id")
            if nid:
                all_nodes[nid] = node

for section_key in ("bootstrap", "tool_discovery"):
    sec = dag.get(section_key, {})
    collect_nodes(sec)

pf = dag.get("phase_features", {})
collect_nodes(pf)
for mod in pf.get("modules", []):
    mid = mod.get("id")
    if mid:
        all_nodes[mid] = mod

# ── Section membership (for the derived: rewrite output) ──────────────────────
node_section = {}
for sk in ("bootstrap", "tool_discovery"):
    for n in dag.get(sk, {}).get("nodes", []):
        if n.get("id"):
            node_section[n["id"]] = sk
for n in pf.get("nodes", []):
    if n.get("id"):
        node_section[n["id"]] = "phase_features"
for m in pf.get("modules", []):
    if m.get("id"):
        node_section[m["id"]] = "phase_features"

# ── 4a. Acyclic check (Kahn's algorithm) ──────────────────────────────────────
graph    = defaultdict(set)
in_degree = defaultdict(int)

for nid, node in all_nodes.items():
    in_degree.setdefault(nid, 0)
    for dep in node.get("depends_on", []):
        if dep in all_nodes:
            graph[dep].add(nid)
            in_degree[nid] += 1

queue = deque(n for n in all_nodes if in_degree[n] == 0)
visited = 0
while queue:
    n = queue.popleft()
    visited += 1
    for nbr in graph[n]:
        in_degree[nbr] -= 1
        if in_degree[nbr] == 0:
            queue.append(nbr)

if visited < len(all_nodes):
    cycle_nodes = [n for n in all_nodes if in_degree[n] > 0]
    issues.append(("FAIL", f"acyclic: cycle detected involving nodes: {cycle_nodes}"))
else:
    issues.append(("PASS", "acyclic: no cycles detected"))

# ── 4b. Nodes resolve to artifacts ────────────────────────────────────────────
# Honors check.paths (a list) with type file|dir|glob (+min_matches for glob);
# falls back to the legacy singular check.path. Nodes with no check block or no
# paths are skipped (e.g. phase_features modules carry no check).
import glob as glob_mod
for nid, node in all_nodes.items():
    check = node.get("check", {})
    if not check:
        continue
    check_type = check.get("type", "")
    paths = check.get("paths")
    if paths is None:
        single = check.get("path", "")
        paths = [single] if single else []
    if not paths:
        continue
    if check_type == "glob":
        min_matches = int(check.get("min_matches", 1))
        total = sum(len(glob_mod.glob(str(repo_root / pat))) for pat in paths)
        if total >= min_matches:
            issues.append(("PASS", f"nodes_resolve[{nid}]: globs {paths} match {total} file(s) (need {min_matches})"))
        else:
            issues.append(("FAIL", f"nodes_resolve[{nid}]: globs {paths} match {total} file(s), need {min_matches}"))
    else:
        missing = []
        for p in paths:
            full = repo_root / p
            ok = full.is_dir() if check_type == "dir" else full.exists()
            if not ok:
                missing.append(p)
        if not missing:
            issues.append(("PASS", f"nodes_resolve[{nid}]: all {len(paths)} path(s) resolve ({check_type or 'exists'})"))
        else:
            issues.append(("FAIL", f"nodes_resolve[{nid}]: missing {missing} (type {check_type or 'exists'})"))

# ── 4c. Execution-log respects topology ───────────────────────────────────────
if log_path.exists():
    log_entries = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    log_entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    logged_nodes_in_order = [e.get("node") for e in log_entries if e.get("node")]
    logged_set = set(logged_nodes_in_order)

    for i, nid in enumerate(logged_nodes_in_order):
        node = all_nodes.get(nid, {})
        for dep in node.get("depends_on", []):
            dep_node = all_nodes.get(dep, {})
            dep_check = dep_node.get("check", {})

            # Dependency satisfied if:
            # (a) it appears earlier in the log, or
            # (b) it has a check block that passes on disk (kind: filesystem / skill_generated)
            in_log_before = dep in logged_set and \
                            logged_nodes_in_order.index(dep) < i

            disk_satisfied = False
            if dep_check:
                # Honor check.paths (list) + type dir|file|glob (+min_matches);
                # fall back to legacy singular check.path. Same logic as 4b so a
                # filesystem ancestor (scaffold/prd/architecture) counts as
                # satisfied even when it carries no execution-log entry (§4.3).
                dep_type = dep_check.get("type", "")
                dep_paths = dep_check.get("paths")
                if dep_paths is None:
                    _single = dep_check.get("path", "")
                    dep_paths = [_single] if _single else []
                if dep_paths:
                    if dep_type == "glob":
                        _min = int(dep_check.get("min_matches", 1))
                        _total = sum(len(glob_mod.glob(str(repo_root / p))) for p in dep_paths)
                        disk_satisfied = _total >= _min
                    else:
                        disk_satisfied = all(
                            (repo_root / p).is_dir() if dep_type == "dir"
                            else (repo_root / p).exists()
                            for p in dep_paths
                        )

            if not in_log_before and not disk_satisfied:
                issues.append(("FAIL",
                    f"exec_order: node '{nid}' logged before its dependency '{dep}' "
                    f"(dep not in log and check did not pass on disk)"))
            elif not in_log_before and disk_satisfied:
                pass  # satisfied by filesystem — no log entry needed
            else:
                pass  # ordered correctly in log

    if log_entries:
        issues.append(("PASS", f"execution_order: {len(log_entries)} log entries checked"))
    else:
        issues.append(("PASS", "execution_order: empty log (no violations)"))
else:
    issues.append(("PASS", "execution_order: execution-log.jsonl absent (no violations to check)"))

# ── 4d. Derived groups consistency (warn only) ────────────────────────────────
# Recompute parallel groups from edges; compare to dag.yaml derived.parallel_group values.
# Compute BFS-level for each node (max depth from a root).
levels = {}
queue = deque()
for nid in all_nodes:
    if in_degree.get(nid, 0) == 0:  # Note: in_degree was modified by Kahn — recompute
        levels[nid] = 0
        queue.append(nid)

# Recompute in_degree for level assignment
in_deg2 = defaultdict(int)
for nid, node in all_nodes.items():
    in_deg2.setdefault(nid, 0)
    for dep in node.get("depends_on", []):
        if dep in all_nodes:
            in_deg2[nid] += 1

level_queue = deque((n, 0) for n in all_nodes if in_deg2[n] == 0)
computed_levels = {}
while level_queue:
    n, lvl = level_queue.popleft()
    computed_levels[n] = max(computed_levels.get(n, 0), lvl)
    for nbr in graph[n]:
        level_queue.append((nbr, computed_levels[n] + 1))

stale = False
for nid, node in all_nodes.items():
    derived = node.get("derived", {})
    stored_group = derived.get("parallel_group")
    if stored_group is not None and nid in computed_levels:
        if int(stored_group) != computed_levels[nid]:
            warnings.append(f"derived_groups[{nid}]: stored group {stored_group} != computed {computed_levels[nid]}")
            stale = True

if stale:
    warnings.append("derived_groups: parallel_group values are stale; dag.sh will rewrite them")
else:
    issues.append(("PASS", "derived_groups: parallel_group values consistent with computed topology"))

# ── 4e. Regenerate derived: block (only with --write, only if no FAIL) ────────
# Deterministic global topological order; parallel_group = longest-path level
# (computed_levels from 4d). Marker-splice keeps every comment above the marker.
def _render_derived():
    indeg = {n: 0 for n in all_nodes}
    for nid_, node_ in all_nodes.items():
        for dep_ in node_.get("depends_on", []):
            if dep_ in all_nodes:
                indeg[nid_] += 1
    ready = [n for n in all_nodes if indeg[n] == 0]
    topo = {}
    idx = 0
    while ready:
        ready.sort(key=lambda n: (computed_levels.get(n, 0), n))
        n = ready.pop(0)
        topo[n] = idx
        idx += 1
        for nbr in sorted(graph[n]):
            indeg[nbr] -= 1
            if indeg[nbr] == 0:
                ready.append(nbr)
    out = ["derived:", f'  generated_at: "{generated_at}"']
    for section in ("bootstrap", "tool_discovery", "phase_features"):
        sec_nodes = [n for n in all_nodes if node_section.get(n) == section]
        if not sec_nodes:
            continue
        out.append(f"  {section}:")
        for n in sorted(sec_nodes, key=lambda x: topo.get(x, 0)):
            out.append(f"    - id: {n}")
            out.append(f"      topological_order: {topo.get(n, 0)}")
            out.append(f"      parallel_group: {computed_levels.get(n, 0)}")
    return "\n".join(out)

if write_mode and not any(sev == "FAIL" for sev, _ in issues):
    text = dag_path.read_text()
    lines = text.split("\n")
    marker_idx = next((i for i, ln in enumerate(lines)
                       if "DERIVED FIELDS" in ln and ln.lstrip().startswith("#")), None)
    if marker_idx is not None:
        head = "\n".join(lines[:marker_idx + 1])
        dag_path.write_text(head + "\n" + _render_derived() + "\n")
        warnings.append("derived: regenerated by --write (block below the marker replaced)")
    else:
        warnings.append("derived: --write set but no '# ── DERIVED FIELDS' marker found; nothing written")
elif write_mode:
    warnings.append("derived: --write set but FAILs present; refusing to regenerate")

# ── Output results ────────────────────────────────────────────────────────────
result = {"issues": issues, "warnings": warnings}
print(json.dumps(result))
PYEOF
)

# Parse Python output
ISSUES=$(echo "$DAG_RESULT" | $PYTHON_CMD -c "
import json, sys
d = json.load(sys.stdin)
for sev, msg in d['issues']:
    print(f'{sev}|{msg}')
" 2>/dev/null || true)

WARNINGS=$(echo "$DAG_RESULT" | $PYTHON_CMD -c "
import json, sys
d = json.load(sys.stdin)
for w in d['warnings']:
    print(w)
" 2>/dev/null || true)

while IFS='|' read -r sev msg; do
    [[ -z "$sev" ]] && continue
    case "$sev" in
        PASS) pass "$msg" ;;
        FAIL) fail "$msg" ;;
        WARN) warn "$msg" ;;
    esac
done <<< "$ISSUES"

while IFS= read -r wmsg; do
    [[ -z "$wmsg" ]] && continue
    warn "$wmsg"
done <<< "$WARNINGS"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
for msg in "${MESSAGES[@]}"; do echo "$msg"; done
echo ""
echo "Gate 4 dag: PASS=$PASS  FAIL=$FAIL  WARN=$WARN"

if [[ "$FAIL" -gt 0 ]]; then
    echo "FAIL gate=dag"
    exit 1
else
    echo "PASS gate=dag"
    exit 0
fi
