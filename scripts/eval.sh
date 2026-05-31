#!/usr/bin/env bash
# scripts/eval.sh — Quality Gate Orchestrator
#
# Runs all eight quality gates in the declared order (fast → slow, no-infra → infra).
# Gate order per AGENT_DRIVEN_DEVELOPMENT.md §7:
#
#   1. static        ruff + mypy + secrets + layer-isolation  (no infra)
#   2. unit          pytest + coverage + assertion density     (no infra)
#   3. contracts     contract-ID grep coverage                 (no infra)
#   4. dag           topology + execution-log integrity        (no infra)
#   5. build         uv build + docker build                   (no infra)
#   6. supply-chain  pip-audit                                 (no infra)
#   7. integration   MinIO + Redis + arq testcontainers        (infra)
#   8. e2e           full upload → worker → query round-trip   (infra)
#
# Flags:
#   --no-infra       Run only gates 1–6 (within the 60 s "fast" budget).
#   --keep-going     Do not stop on first failure; run all selected gates.
#   --built-only     Passed through to contracts.sh (scope checks to modules
#                    with code on disk).
#   --gate=<name>    Run only the named gate (can repeat; e.g. --gate=unit --gate=dag).
#
# Exit codes:
#   0  All gates passed (or all were skipped).
#   1  One or more gates failed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATES_DIR="$REPO_ROOT/scripts/gates"

# ── Parse arguments ────────────────────────────────────────────────────────────
NO_INFRA=false
KEEP_GOING=false
BUILT_ONLY=false
SELECTED_GATES=()

for arg in "$@"; do
    case "$arg" in
        --no-infra)    NO_INFRA=true ;;
        --keep-going)  KEEP_GOING=true ;;
        --built-only)  BUILT_ONLY=true ;;
        --gate=*)      SELECTED_GATES+=("${arg#--gate=}") ;;
        -h|--help)
            echo "Usage: eval.sh [--no-infra] [--keep-going] [--built-only] [--gate=<name>...]"
            echo ""
            echo "  --no-infra       Run only gates 1–6 (no MinIO/Redis required)."
            echo "  --keep-going     Continue after a failure instead of stopping."
            echo "  --built-only     Pass --built-only to contracts.sh."
            echo "  --gate=<name>    Run only the named gate(s)."
            echo ""
            echo "Gates (in order): static unit contracts dag build supply-chain integration e2e"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg (use --help for usage)" >&2
            exit 1
            ;;
    esac
done

# ── Gate definitions (name, script, needs_infra) ──────────────────────────────
# Declared in §7 order: gates 1–6 no-infra, 7–8 infra.
declare -a GATE_NAMES=(static unit contracts dag build supply-chain integration e2e)
declare -A GATE_NEEDS_INFRA=(
    [static]=false
    [unit]=false
    [contracts]=false
    [dag]=false
    [build]=false
    [supply-chain]=false
    [integration]=true
    [e2e]=true
)
declare -A GATE_SCRIPTS=(
    [static]="$GATES_DIR/static.sh"
    [unit]="$GATES_DIR/unit.sh"
    [contracts]="$GATES_DIR/contracts.sh"
    [dag]="$GATES_DIR/dag.sh"
    [build]="$GATES_DIR/build.sh"
    [supply-chain]="$GATES_DIR/supply-chain.sh"
    [integration]="$GATES_DIR/integration.sh"
    [e2e]="$GATES_DIR/e2e.sh"
)

# ── Select gates to run ───────────────────────────────────────────────────────
GATES_TO_RUN=()
if [[ ${#SELECTED_GATES[@]} -gt 0 ]]; then
    # Explicit gate selection — validate names and preserve declared order.
    for gate in "${GATE_NAMES[@]}"; do
        for selected in "${SELECTED_GATES[@]}"; do
            if [[ "$gate" == "$selected" ]]; then
                GATES_TO_RUN+=("$gate")
                break
            fi
        done
    done
    # Report unknown gate names
    for selected in "${SELECTED_GATES[@]}"; do
        found=false
        for gate in "${GATE_NAMES[@]}"; do
            [[ "$gate" == "$selected" ]] && found=true && break
        done
        if [[ "$found" == "false" ]]; then
            echo "Unknown gate: '$selected'. Valid gates: ${GATE_NAMES[*]}" >&2
            exit 1
        fi
    done
else
    for gate in "${GATE_NAMES[@]}"; do
        if [[ "$NO_INFRA" == "true" && "${GATE_NEEDS_INFRA[$gate]}" == "true" ]]; then
            continue   # skip infra gates when --no-infra
        fi
        GATES_TO_RUN+=("$gate")
    done
fi

# ── Timing helpers ─────────────────────────────────────────────────────────────
EVAL_START=$(date +%s)

# ── Results table ─────────────────────────────────────────────────────────────
declare -A GATE_RESULT    # PASS | FAIL | SKIP
declare -A GATE_DURATION  # seconds

OVERALL_PASS=true

# ── Print header ──────────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  PageIndex MCP Server — Quality Gate Suite                      ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
printf "║  Mode: %-59s║\n" "$( [[ "$NO_INFRA" == true ]] && echo 'no-infra (gates 1–6)' || echo 'full (gates 1–8)' )"
printf "║  Date: %-59s║\n" "$(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# ── Run gates ─────────────────────────────────────────────────────────────────
for gate in "${GATES_TO_RUN[@]}"; do
    script="${GATE_SCRIPTS[$gate]}"

    if [[ ! -x "$script" ]]; then
        echo "⚠  Gate '$gate': script not executable or missing: $script"
        GATE_RESULT[$gate]="SKIP"
        GATE_DURATION[$gate]=0
        continue
    fi

    echo "──────────────────────────────────────────────────────────────────"

    GATE_START=$(date +%s)

    # Build per-gate extra args
    GATE_EXTRA_ARGS=()
    if [[ "$gate" == "contracts" && "$BUILT_ONLY" == "true" ]]; then
        GATE_EXTRA_ARGS+=("--built-only")
    fi

    # Run the gate; capture exit code without triggering set -e
    if "$script" "${GATE_EXTRA_ARGS[@]+"${GATE_EXTRA_ARGS[@]}"}"; then
        GATE_EXIT=0
    else
        GATE_EXIT=$?
    fi

    GATE_END=$(date +%s)
    GATE_DURATION[$gate]=$((GATE_END - GATE_START))

    # Determine result from exit code and last-line output convention.
    # Gates print "PASS gate=<name>", "FAIL gate=<name>", or "SKIP gate=<name>"
    # as their final line. We trust exit code as the authoritative signal.
    if [[ "$GATE_EXIT" -eq 0 ]]; then
        GATE_RESULT[$gate]="PASS"
        echo ""
        echo "  Gate '$gate' → PASS  (${GATE_DURATION[$gate]}s)"
    else
        GATE_RESULT[$gate]="FAIL"
        echo ""
        echo "  Gate '$gate' → FAIL  (${GATE_DURATION[$gate]}s)"
        OVERALL_PASS=false

        if [[ "$KEEP_GOING" == "false" ]]; then
            echo ""
            echo "Stopping on first failure (use --keep-going to run all gates)."
            # Print partial summary before exiting
            break
        fi
    fi
done

# ── Summary table ──────────────────────────────────────────────────────────────
EVAL_END=$(date +%s)
EVAL_ELAPSED=$((EVAL_END - EVAL_START))

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  Gate Summary                                                    ║"
echo "╠═══════════════╦════════════╦═══════════╗                        ║"
echo "║  Gate         ║  Result    ║  Duration ║                        ║"
echo "╠═══════════════╬════════════╬═══════════╣                        ║"

for gate in "${GATE_NAMES[@]}"; do
    result="${GATE_RESULT[$gate]:-skipped}"
    duration="${GATE_DURATION[$gate]:-—}"
    [[ "$duration" != "—" ]] && duration="${duration}s"

    case "$result" in
        PASS)    symbol="✓" ;;
        FAIL)    symbol="✗" ;;
        skipped) symbol="—" ;;
        SKIP)    symbol="○" ;;
        *)       symbol="?" ;;
    esac

    printf "║  %-13s ║  %s %-7s  ║  %-8s ║\n" \
        "$gate" "$symbol" "$result" "$duration"
done

echo "╠═══════════════╩════════════╩═══════════╝                        ║"
printf "║  Total elapsed: %-49s║\n" "${EVAL_ELAPSED}s"
echo "╚══════════════════════════════════════════════════════════════════╝"

echo ""
if [[ "$OVERALL_PASS" == "true" ]]; then
    echo "PASS eval.sh  — all selected gates passed."
    exit 0
else
    FAILED_GATES=()
    for gate in "${GATE_NAMES[@]}"; do
        [[ "${GATE_RESULT[$gate]:-}" == "FAIL" ]] && FAILED_GATES+=("$gate")
    done
    echo "FAIL eval.sh  — failed gates: ${FAILED_GATES[*]}"
    exit 1
fi
