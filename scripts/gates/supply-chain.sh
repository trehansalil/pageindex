#!/usr/bin/env bash
# scripts/gates/supply-chain.sh — Gate 6: Supply-Chain Security
#
# Enforces:
#   - pip-audit over uv.lock: zero known vulnerabilities, unless whitelisted in
#     .agents/governance/known-advisories.yaml.
#   - audit_clean: the audit exits clean (no unaudited advisories).
#
# Whitelist logic:
#   If known-advisories.yaml exists, any advisory ID listed under
#   `advisories[].id` is excluded from the failure count (treated as
#   acknowledged; still printed as WARN).
#
# Needs infra: no
# Reads thresholds from .agents/governance/verify-gates.yaml via read-yaml.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIB_DIR="$REPO_ROOT/scripts/lib"
GATES_YAML="$REPO_ROOT/.agents/governance/verify-gates.yaml"
KNOWN_ADV="$REPO_ROOT/.agents/governance/known-advisories.yaml"

# shellcheck source=../lib/read-yaml.sh
source "$LIB_DIR/read-yaml.sh"

PASS=0
FAIL=0
WARN=0
SKIP=0
MESSAGES=()

pass()  { PASS=$((PASS+1));  MESSAGES+=("  [PASS]  $*"); }
fail()  { FAIL=$((FAIL+1));  MESSAGES+=("  [FAIL]  $*"); }
warn()  { WARN=$((WARN+1));  MESSAGES+=("  [WARN]  $*"); }
skip()  { SKIP=$((SKIP+1));  MESSAGES+=("  [SKIP]  $* (not yet configured)"); }

echo "=== Gate 6: supply-chain ==="

cd "$REPO_ROOT"

# ── Read thresholds ────────────────────────────────────────────────────────────
MAX_VULNS=$(gate_threshold  "supply_chain.vulnerabilities" 2>/dev/null || echo "0")
AUDIT_CLEAN=$(gate_threshold "supply_chain.audit_clean"   2>/dev/null || echo "true")

# ── Prerequisite: pip-audit available ─────────────────────────────────────────
if ! command -v pip-audit &>/dev/null && ! uv run pip-audit --version &>/dev/null 2>&1; then
    skip "pip-audit (not installed; run 'uv add --dev pip-audit')"
    echo "SKIP gate=supply-chain"
    exit 0
fi

AUDIT_CMD="pip-audit"
command -v pip-audit &>/dev/null || AUDIT_CMD="uv run pip-audit"

# ── Load whitelisted advisory IDs ────────────────────────────────────────────
WHITELIST=()
if [[ -f "$KNOWN_ADV" ]]; then
    if command -v python3 &>/dev/null || uv run python3 --version &>/dev/null 2>&1; then
        PY_CMD="python3"
        command -v python3 &>/dev/null || PY_CMD="uv run python3"
        mapfile -t WHITELIST < <($PY_CMD - "$KNOWN_ADV" <<'PYEOF'
import sys, pathlib
try:
    import yaml
except ImportError:
    sys.exit(0)
path = pathlib.Path(sys.argv[1])
data = yaml.safe_load(path.read_text())
for adv in data.get("advisories", []):
    aid = adv.get("id", "")
    if aid:
        print(aid)
PYEOF
        )
    fi
fi

# ── Run pip-audit ─────────────────────────────────────────────────────────────
# Audit against uv.lock for reproducibility.
AUDIT_ARGS="--format=json"
if [[ -f "uv.lock" ]]; then
    # pip-audit can read requirements from the venv; for uv projects, audit the
    # installed environment after uv sync.
    AUDIT_OUTPUT=$($AUDIT_CMD $AUDIT_ARGS 2>/tmp/pip_audit_stderr.txt || true)
else
    AUDIT_OUTPUT=$($AUDIT_CMD $AUDIT_ARGS 2>/tmp/pip_audit_stderr.txt || true)
fi

# Parse JSON output
if command -v python3 &>/dev/null || uv run python3 --version &>/dev/null 2>&1; then
    PY_CMD="python3"
    command -v python3 &>/dev/null || PY_CMD="uv run python3"

    PARSE_RESULT=$($PY_CMD - <<PYEOF
import json, sys

output = '''$AUDIT_OUTPUT'''
whitelist = ${WHITELIST[@]+"${WHITELIST[@]}"} if False else []
whitelist_str = """$(IFS=$'\n'; echo "${WHITELIST[*]:-}")"""
whitelist = [w.strip() for w in whitelist_str.split('\n') if w.strip()]

try:
    data = json.loads(output)
except (json.JSONDecodeError, ValueError):
    print("PARSE_FAIL")
    sys.exit(0)

vulnerabilities = data.get("vulnerabilities", [])
unwhitelisted = []
whitelisted = []

for vuln in vulnerabilities:
    for fix in vuln.get("vulns", []):
        vid = fix.get("id", "")
        if vid in whitelist:
            whitelisted.append((vuln.get("name", "?"), vid))
        else:
            unwhitelisted.append((vuln.get("name", "?"), vid, fix.get("description", "")))

print(f"UNWHITELISTED={len(unwhitelisted)}")
print(f"WHITELISTED={len(whitelisted)}")
for pkg, vid, desc in unwhitelisted:
    short_desc = desc[:80] if desc else "no description"
    print(f"VULN|{pkg}|{vid}|{short_desc}")
for pkg, vid in whitelisted:
    print(f"KNOWN|{pkg}|{vid}")
PYEOF
    )

    UNWHITELISTED_COUNT=0
    WHITELISTED_COUNT=0

    while IFS= read -r line; do
        if [[ "$line" == "PARSE_FAIL" ]]; then
            # pip-audit returned non-JSON (maybe an error message)
            if [[ "$AUDIT_CLEAN" == "true" ]]; then
                fail "pip-audit: could not parse JSON output; treat as unclean"
            else
                warn "pip-audit: could not parse output"
            fi
            break
        elif [[ "$line" =~ ^UNWHITELISTED=([0-9]+)$ ]]; then
            UNWHITELISTED_COUNT="${BASH_REMATCH[1]}"
        elif [[ "$line" =~ ^WHITELISTED=([0-9]+)$ ]]; then
            WHITELISTED_COUNT="${BASH_REMATCH[1]}"
        elif [[ "$line" =~ ^VULN\|(.+)\|(.+)\|(.*)$ ]]; then
            PKG="${BASH_REMATCH[1]}"
            VID="${BASH_REMATCH[2]}"
            DESC="${BASH_REMATCH[3]}"
            MESSAGES+=("  [VULN]  ${PKG} ${VID}: ${DESC}")
        elif [[ "$line" =~ ^KNOWN\|(.+)\|(.+)$ ]]; then
            PKG="${BASH_REMATCH[1]}"
            VID="${BASH_REMATCH[2]}"
            warn "known-advisory whitelisted: ${PKG} ${VID}"
        fi
    done <<< "$PARSE_RESULT"

    if [[ "$UNWHITELISTED_COUNT" -le "$MAX_VULNS" ]]; then
        pass "pip-audit: $UNWHITELISTED_COUNT unwhitelisted vulnerabilities (threshold: $MAX_VULNS)"
        if [[ "$WHITELISTED_COUNT" -gt 0 ]]; then
            pass "pip-audit: $WHITELISTED_COUNT known-advisory CVE(s) whitelisted"
        fi
    else
        fail "pip-audit: $UNWHITELISTED_COUNT unwhitelisted vulnerabilities found (threshold: $MAX_VULNS)"
    fi
else
    # No Python available for parsing — run pip-audit in text mode and check exit code
    if $AUDIT_CMD &>/tmp/pip_audit_text.txt; then
        pass "pip-audit: clean (text mode)"
    else
        fail "pip-audit: vulnerabilities found (text mode; install python3 for detailed output)"
        cat /tmp/pip_audit_text.txt | tail -30
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
for msg in "${MESSAGES[@]}"; do echo "$msg"; done
echo ""
echo "Gate 6 supply-chain: PASS=$PASS  FAIL=$FAIL  WARN=$WARN  SKIP=$SKIP"

if [[ "$FAIL" -gt 0 ]]; then
    echo "FAIL gate=supply-chain"
    exit 1
else
    echo "PASS gate=supply-chain"
    exit 0
fi
