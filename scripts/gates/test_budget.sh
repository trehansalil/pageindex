#!/usr/bin/env bash
# scripts/gates/test_budget.sh — Gate: Test Budget (collected-count ratchet)
#
# Enforces ONE number: how many tests `pytest --collect-only` finds.
#
# Why a count and not a ratio
# ---------------------------
# `unit.max_test_file_ratio` already guards test FILES. It went RED on
# 2026-09-02 (commit 040aaae, ratio 0.68) and stayed red through 2026-09-22
# (0.72) while the suite grew 1393 -> 2510 collected, and nobody noticed,
# because (a) no CI workflow ran any gate and (b) a file ratio cannot see
# 1100 new tests arriving inside the files that already exist. 27 files can
# hold 3000 tests and the ratio gate stays green. This gate counts the thing
# that actually grew.
#
# The allowance — why 15, and why an absolute number
# --------------------------------------------------
# A percentage is the wrong shape here: 5% of 941 is 47 today and 125 once the
# suite has drifted to 2500, i.e. the budget loosens fastest exactly when it is
# failing hardest. An absolute headroom does the opposite — it stays the same
# size while the suite grows, so each wave feels the squeeze sooner.
# 15 is deliberately about one feature branch's worth of genuinely new cases
# (~1.6% of 941): enough that adding three tests for a bug fix does not demand a
# baseline edit, small enough that the observed growth rate (roughly +100
# collected per RFC wave) trips it on the first wave instead of the twentieth.
# The allowance is NOT a budget to spend every branch — it is churn tolerance.
# Amending it is an RFC decision (agents/governance/verify-gates.yaml).
#
# Ratchet direction
# -----------------
#   collected > baseline + allowance  -> FAIL. Collapse tests, or raise the
#                                        baseline in the SAME commit with a
#                                        one-line justification.
#   collected < baseline - allowance  -> the baseline is stale-high. The file is
#                                        rewritten in the working tree and the
#                                        gate fails ONCE, asking you to commit
#                                        it. That locks a reduction in so it
#                                        cannot silently reverse.
#   otherwise                         -> PASS.
#
# Host safety
# -----------
# NEVER runs the suite — only `--collect-only`. Always `timeout`-wrapped, and
# run inside a systemd memory-capped scope when one can be obtained (same
# pattern as `make test`; see the Makefile banner for the 2026-09-17 outage
# this protects against). `pytest-timeout` is NOT installed; `--timeout=` would
# fail the run and must not be added.
#
# Needs infra: no
# Reads thresholds from agents/governance/verify-gates.yaml via read-yaml.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIB_DIR="$REPO_ROOT/scripts/lib"
GATES_YAML="$REPO_ROOT/agents/governance/verify-gates.yaml"

# shellcheck source=../lib/read-yaml.sh
source "$LIB_DIR/read-yaml.sh"

cd "$REPO_ROOT"

echo "=== Gate: test-budget ==="

# ── Thresholds ────────────────────────────────────────────────────────────────
BASELINE_FILE_REL=$(gate_threshold "unit.test_budget_baseline_file" 2>/dev/null || echo "tests/TEST_BUDGET.baseline")
ALLOWANCE=$(gate_threshold        "unit.test_budget_allowance"      2>/dev/null || echo "15")
BASELINE_FILE="$REPO_ROOT/$BASELINE_FILE_REL"
COLLECT_MEM_MAX="${TEST_MEM_MAX:-3G}"
COLLECT_TIMEOUT="${COLLECT_TIMEOUT:-600}"

if [[ ! -f "$BASELINE_FILE" ]]; then
    echo "  [FAIL]  baseline file missing: $BASELINE_FILE_REL"
    echo "FAIL gate=test-budget"
    exit 1
fi

BASELINE=$(grep -vE '^[[:space:]]*(#|$)' "$BASELINE_FILE" | head -1 | tr -cd '0-9')
if [[ -z "$BASELINE" ]]; then
    echo "  [FAIL]  $BASELINE_FILE_REL contains no baseline integer"
    echo "FAIL gate=test-budget"
    exit 1
fi

# ── Count: collect only, never run ────────────────────────────────────────────
COLLECT_LOG="$(mktemp -t test-budget-collect.XXXXXX)"
trap 'rm -f "$COLLECT_LOG"' EXIT

COLLECT_CMD='exec timeout '"$COLLECT_TIMEOUT"' uv run pytest --collect-only -q'

# Prefer a memory-capped cgroup scope. Probe first: systemd-run is absent on CI
# runners and needs polkit for an unprivileged --scope, and a hard dependency
# would make the gate unrunnable there.
if command -v systemd-run >/dev/null 2>&1 \
   && systemd-run --scope --quiet --collect true >/dev/null 2>&1; then
    echo "  [info]  collecting inside a systemd scope (MemoryMax=$COLLECT_MEM_MAX, MemorySwapMax=0)"
    set +e
    systemd-run --scope --quiet --collect \
        -p MemoryMax="$COLLECT_MEM_MAX" -p MemorySwapMax=0 \
        sh -c "echo 900 > /proc/self/oom_score_adj; $COLLECT_CMD" >"$COLLECT_LOG" 2>&1
    COLLECT_EXIT=$?
    set -e
else
    echo "  [info]  systemd scope unavailable — collecting under timeout ${COLLECT_TIMEOUT}s only"
    set +e
    sh -c "$COLLECT_CMD" >"$COLLECT_LOG" 2>&1
    COLLECT_EXIT=$?
    set -e
fi

if [[ "$COLLECT_EXIT" -eq 124 ]]; then
    echo "  [FAIL]  collection timed out after ${COLLECT_TIMEOUT}s"
    echo "FAIL gate=test-budget"
    exit 1
fi

# A collection ERROR undercounts, which would let the ratchet pass while the
# suite is broken. Treat it as a failure, not as a low number.
COLLECTED=$(grep -oE '[0-9]+ tests? collected' "$COLLECT_LOG" | tail -1 | grep -oE '^[0-9]+' || true)
ERRORS=$(grep -oE '[0-9]+ errors?' "$COLLECT_LOG" | tail -1 | grep -oE '^[0-9]+' || true)

if [[ -z "$COLLECTED" ]]; then
    echo "  [FAIL]  could not parse a collected count from pytest output:"
    tail -20 "$COLLECT_LOG" | sed 's/^/          /'
    echo "FAIL gate=test-budget"
    exit 1
fi

if [[ -n "$ERRORS" && "$ERRORS" -gt 0 ]]; then
    echo "  [FAIL]  ${ERRORS} collection error(s) — the count ${COLLECTED} is not trustworthy."
    echo "          Fix collection first; a broken import must not read as a shrinking suite."
    tail -20 "$COLLECT_LOG" | sed 's/^/          /'
    echo "FAIL gate=test-budget"
    exit 1
fi

CEILING=$(( BASELINE + ALLOWANCE ))
FLOOR=$(( BASELINE - ALLOWANCE ))

echo "  [info]  collected=${COLLECTED}  baseline=${BASELINE}  allowance=±${ALLOWANCE}  ceiling=${CEILING}"

# ── Over budget ───────────────────────────────────────────────────────────────
if [[ "$COLLECTED" -gt "$CEILING" ]]; then
    OVER=$(( COLLECTED - BASELINE ))
    cat <<MSG

  [FAIL]  test budget exceeded: ${COLLECTED} collected, baseline ${BASELINE} (+${OVER}, allowance ${ALLOWANCE})

  Two ways forward, and only two:

    1. COLLAPSE. Most growth in this repo has been near-duplicate cases that
       belong in one @pytest.mark.parametrize on a test that already exists,
       or a static invariant that belongs in scripts/gates/source_invariants.py
       rather than in a collected test. Try that first — it is why the suite
       fits in 27 files at all.

    2. RAISE THE BASELINE, in the SAME COMMIT, with a justification:

         edit ${BASELINE_FILE_REL}
           - change the number to ${COLLECTED}
           - append one history line, e.g.
             # $(date +%Y-%m-%d)  ${COLLECTED}  RFC-0XX task N.N: <why these N tests could not be parametrized into an existing case>

       That is the whole point. A reviewer then sees a +${OVER} line in the diff
       and gets to ask the question nobody got to ask between 1393 and 2510.

  Do NOT raise agents/governance/verify-gates.yaml#gates.unit.test_budget_allowance
  to get past this. Thresholds are amendable only via RFC.

MSG
    echo "FAIL gate=test-budget"
    exit 1
fi

# ── Under budget: ratchet down ────────────────────────────────────────────────
if [[ "$COLLECTED" -lt "$FLOOR" ]]; then
    DROP=$(( BASELINE - COLLECTED ))
    python3 - "$BASELINE_FILE" "$BASELINE" "$COLLECTED" <<'PY'
import datetime, pathlib, sys

path, old, new = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = path.read_text().splitlines()
# The baseline integer is the single non-comment, non-blank line.
out, replaced = [], False
for line in lines:
    if not replaced and line.strip() and not line.lstrip().startswith("#"):
        today = datetime.date.today().isoformat()
        out.append(
            f"# {today}  {new}  ratcheted down automatically from {old} "
            f"(-{int(old) - int(new)}); reduction locked in by scripts/gates/test_budget.sh."
        )
        out.append(new)
        replaced = True
    else:
        out.append(line)
path.write_text("\n".join(out) + "\n")
PY
    cat <<MSG

  [FAIL]  baseline was stale-high and has been LOWERED for you.

          ${BASELINE} -> ${COLLECTED}  (-${DROP})

  ${BASELINE_FILE_REL} has been rewritten in your working tree. Commit it in
  this same commit. This fails exactly once: re-run and it passes.

  Why this is a failure and not a shrug: a reduction that is not committed
  leaves the old, higher number in the repo, and the suite can silently regrow
  all the way back to it. That is how 977 became 2510. Locking the floor down
  is the half of a ratchet that makes the other half mean anything.

MSG
    echo "FAIL gate=test-budget"
    exit 1
fi

echo ""
echo "  [PASS]  test budget: ${COLLECTED} collected, within ${BASELINE} ±${ALLOWANCE}"
echo ""
echo "PASS gate=test-budget"
exit 0
