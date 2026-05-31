#!/usr/bin/env bash
# scripts/gates/contracts.sh — Gate 3: Behavioral Contract Coverage
#
# Enforces:
#   - Every .agents/contracts/*.yaml file contains contract IDs that are
#     grep-found in tests/ (function names, marker comments, or docstrings).
#   - Every module with code on disk has at least one contract YAML (unless
#     --built-only is given, in which case only modules with src files are checked).
#   - meta: true contracts are exempt from grep verification.
#   - storage_write_requires_integration: warn (not fail) if a storage-write
#     contract is only covered by a unit test, not an integration test.
#
# Flags:
#   --built-only   Scope check to modules that have a corresponding
#                  src/pageindex_mcp/<module>.py on disk (used by eval.sh).
#
# Needs infra: no
# Reads thresholds from .agents/governance/verify-gates.yaml via read-yaml.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIB_DIR="$REPO_ROOT/scripts/lib"
GATES_YAML="$REPO_ROOT/.agents/governance/verify-gates.yaml"

# shellcheck source=../lib/read-yaml.sh
source "$LIB_DIR/read-yaml.sh"

BUILT_ONLY=false
for arg in "$@"; do
    if [[ "$arg" == "--built-only" ]]; then BUILT_ONLY=true; fi
done

PASS=0
FAIL=0
WARN=0
MESSAGES=()

pass()  { PASS=$((PASS+1));  MESSAGES+=("  [PASS]  $*"); }
fail()  { FAIL=$((FAIL+1));  MESSAGES+=("  [FAIL]  $*"); }
warn()  { WARN=$((WARN+1));  MESSAGES+=("  [WARN]  $*"); }

echo "=== Gate 3: contracts (built_only=${BUILT_ONLY}) ==="

cd "$REPO_ROOT"

# ── Read thresholds ────────────────────────────────────────────────────────────
ALL_FEATURES_HAVE_CONTRACTS=$(gate_threshold "contracts.all_features_have_contracts" 2>/dev/null || echo "true")
ALL_CONTRACTS_IN_TESTS=$(gate_threshold      "contracts.all_contracts_in_tests"      2>/dev/null || echo "true")
META_EXEMPT=$(gate_threshold                 "contracts.meta_contract_exempt"          2>/dev/null || echo "true")
STORAGE_WARN=$(gate_threshold               "contracts.storage_write_requires_integration" 2>/dev/null || echo "warn")

CONTRACTS_DIR="$REPO_ROOT/.agents/contracts"

# ── 3a. No contracts directory yet ───────────────────────────────────────────
if [[ ! -d "$CONTRACTS_DIR" ]]; then
    echo "  [SKIP]  .agents/contracts/ directory does not exist yet"
    echo "SKIP gate=contracts (.agents/contracts/ absent; create during RFC-000)"
    exit 0
fi

# ── 3b. all_features_have_contracts ──────────────────────────────────────────
# For every src module on disk (or all if not --built-only), warn if no contract YAML.
#
# Contract-exempt modules: the transport entry point (server — thin FastMCP +
# query-tool layer) and the cross-cutting leaves (auth, config, metrics) own no
# behavioral contract by design — the substantive logic lives in the service /
# repository / helper modules they support. They are PASSed, not FAILed, when they
# carry no contract YAML. NOTE: `helpers` is deliberately NOT exempt — it owns
# RAG-01's prefilter + concurrent-search logic (rag-01.yaml `module: helpers`).
# See RFC-003 §Fixes (Stage-1.5 gate refinement).
CONTRACT_EXEMPT_MODULES=("server" "auth" "config" "metrics")
is_contract_exempt() {
    local m="$1"
    for ex in "${CONTRACT_EXEMPT_MODULES[@]}"; do
        [[ "$m" == "$ex" ]] && return 0
    done
    return 1
}

if [[ "$ALL_FEATURES_HAVE_CONTRACTS" == "true" ]]; then
    MODULES_TO_CHECK=()
    if [[ "$BUILT_ONLY" == "true" ]]; then
        # Only modules that exist on disk
        for src_file in "$REPO_ROOT/src/pageindex_mcp/"*.py; do
            base=$(basename "$src_file" .py)
            [[ "$base" == "__init__" ]] && continue
            MODULES_TO_CHECK+=("$base")
        done
    else
        # All well-known modules
        MODULES_TO_CHECK=("client" "worker" "storage" "cache" "converters" "server" "upload_app" "auth" "config" "metrics" "helpers")
    fi

    for mod in "${MODULES_TO_CHECK[@]}"; do
        # A module is "covered" if any contract YAML mentions it as the module field.
        if compgen -G "$CONTRACTS_DIR/*.yaml" &>/dev/null; then
            FOUND=$( (grep -l "^module:[[:space:]]*${mod}" "$CONTRACTS_DIR/"*.yaml 2>/dev/null || true) | wc -l | tr -d ' ')
        else
            FOUND=0
        fi
        if [[ -f "$REPO_ROOT/src/pageindex_mcp/${mod}.py" ]]; then
            if [[ "$FOUND" -gt 0 ]]; then
                pass "contracts: module '${mod}' has contract YAML"
            elif is_contract_exempt "$mod"; then
                pass "contracts: module '${mod}' contract-exempt (transport/cross-cutting leaf)"
            else
                fail "contracts: module '${mod}' has src file but no contract YAML"
            fi
        fi
    done
fi

# ── 3c. all_contracts_in_tests ────────────────────────────────────────────────
# For every contract ID in every YAML, grep tests/ for the ID.
if [[ "$ALL_CONTRACTS_IN_TESTS" == "true" ]]; then
    if compgen -G "$CONTRACTS_DIR/*.yaml" &>/dev/null; then
        CONTRACT_YAMLS=("$CONTRACTS_DIR/"*.yaml)
    else
        CONTRACT_YAMLS=()
    fi

    if [[ ${#CONTRACT_YAMLS[@]} -eq 0 ]]; then
        echo "  [SKIP]  no contract YAMLs found in .agents/contracts/"
    else
        for yaml_file in "${CONTRACT_YAMLS[@]}"; do
            feature_name=$(grep '^feature:' "$yaml_file" | awk '{print $2}' | tr -d '"' || echo "unknown")

            # Check if this is a meta contract (exempt from grep)
            is_meta=$(grep -c '^meta:[[:space:]]*true' "$yaml_file" 2>/dev/null || true)
            if [[ "$META_EXEMPT" == "true" && "$is_meta" -gt 0 ]]; then
                pass "contracts[${feature_name}]: meta contract — exempt from grep verification"
                continue
            fi

            # Extract all contract IDs (e.g. UPLOAD-01-C1)
            CONTRACT_IDS=$(grep -E '^[[:space:]]*-?[[:space:]]*id:[[:space:]]' "$yaml_file" | sed -E 's/^[[:space:]]*-?[[:space:]]*id:[[:space:]]*//' | tr -d '"' )

            while IFS= read -r cid; do
                [[ -z "$cid" ]] && continue
                # Search for the contract ID in test function names, markers, or comments
                GREP_HITS=$( (grep -r "$cid" "$REPO_ROOT/tests/" 2>/dev/null || true) | wc -l | tr -d ' ')

                if [[ "$GREP_HITS" -gt 0 ]]; then
                    pass "contracts[${cid}]: found in tests/ ($GREP_HITS occurrence(s))"
                else
                    fail "contracts[${cid}]: NOT found in tests/ — write a test containing '${cid}'"

                    # storage_write_requires_integration check: downgrade to warn if
                    # the contract effect describes a storage write
                    if [[ "$STORAGE_WARN" == "warn" ]]; then
                        EFFECT=$( (grep -A5 "id:[[:space:]]*\"\\?${cid}\"\\?" "$yaml_file" || true) \
                            | (grep 'effect:' || true) | head -1 | tr '[:upper:]' '[:lower:]')
                        if echo "$EFFECT" | grep -qE 'minio|write|put|store|upload'; then
                            warn "contracts[${cid}]: storage-write contract — ensure an integration test covers it"
                        fi
                    fi
                fi
            done <<< "$CONTRACT_IDS"
        done
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
for msg in "${MESSAGES[@]}"; do echo "$msg"; done
echo ""
echo "Gate 3 contracts: PASS=$PASS  FAIL=$FAIL  WARN=$WARN"

if [[ "$FAIL" -gt 0 ]]; then
    echo "FAIL gate=contracts"
    exit 1
else
    echo "PASS gate=contracts"
    exit 0
fi
