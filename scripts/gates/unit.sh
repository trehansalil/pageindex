#!/usr/bin/env bash
# scripts/gates/unit.sh — Gate 2: Unit Tests
#
# Enforces: pytest pass, coverage thresholds (default + per-module), assertion density,
# layer test-file existence.
# Needs infra: no (fakeredis + mocks only)
#
# Reads thresholds from .agents/governance/verify-gates.yaml via read-yaml.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIB_DIR="$REPO_ROOT/scripts/lib"
GATES_YAML="$REPO_ROOT/.agents/governance/verify-gates.yaml"

# shellcheck source=../lib/read-yaml.sh
source "$LIB_DIR/read-yaml.sh"

PASS=0
FAIL=0
SKIP=0
MESSAGES=()

pass()  { PASS=$((PASS+1));  MESSAGES+=("  [PASS]  $*"); }
fail()  { FAIL=$((FAIL+1));  MESSAGES+=("  [FAIL]  $*"); }
skip()  { SKIP=$((SKIP+1));  MESSAGES+=("  [SKIP]  $* (not yet configured)"); }

echo "=== Gate 2: unit ==="

cd "$REPO_ROOT"

# ── Read thresholds ────────────────────────────────────────────────────────────
COV_DEFAULT=$(gate_threshold "unit.coverage_default"           2>/dev/null || echo "70")
COV_CLIENT=$(gate_threshold  "unit.coverage_modules.client"    2>/dev/null || echo "90")
COV_STORAGE=$(gate_threshold "unit.coverage_modules.storage"   2>/dev/null || echo "90")
COV_WORKER=$(gate_threshold  "unit.coverage_modules.worker"    2>/dev/null || echo "85")
ASSERT_DENSITY=$(gate_threshold "unit.assertion_density"       2>/dev/null || echo "2.0")
LAYER_EXISTENCE=$(gate_threshold "unit.layer_file_existence"   2>/dev/null || echo "true")

# ── Prerequisite: pytest available ────────────────────────────────────────────
if ! command -v pytest &>/dev/null && ! uv run pytest --version &>/dev/null 2>&1; then
    echo "  [SKIP]  pytest not installed — run 'uv sync --extra dev'"
    echo "SKIP gate=unit (pytest not available)"
    exit 0
fi

PYTEST_CMD="pytest"
command -v pytest &>/dev/null || PYTEST_CMD="uv run pytest"

# ── 2a. pytest run ────────────────────────────────────────────────────────────
# Exclude integration/e2e tests (they need infra); run unit tests with fakeredis.
# Coverage report generated to .coverage + coverage.xml for threshold parsing.
PYTEST_EXTRA_ARGS=""
if python3 -c "import pytest_cov" &>/dev/null 2>&1 || uv run python3 -c "import pytest_cov" &>/dev/null 2>&1; then
    PYTEST_EXTRA_ARGS="--cov=src/pageindex_mcp --cov-report=term-missing --cov-report=xml:coverage.xml"
fi

# Mark-based exclusion: skip tests requiring real infra (tagged @pytest.mark.integration
# or @pytest.mark.e2e) or the staging e2e file.
PYTEST_IGNORE="--ignore=tests/test_staging_e2e.py"

if $PYTEST_CMD $PYTEST_IGNORE -m "not integration and not e2e" $PYTEST_EXTRA_ARGS \
      --tb=short -q 2>&1 | tee /tmp/pytest_unit_output.txt; then
    pass "pytest: all unit tests passed"
    PYTEST_EXIT=0
else
    fail "pytest: one or more unit tests failed"
    PYTEST_EXIT=1
fi

# ── 2b. Coverage thresholds ───────────────────────────────────────────────────
if [[ -f "coverage.xml" ]]; then
    # Extract total line coverage from coverage.xml
    TOTAL_COV=$(python3 -c "
import xml.etree.ElementTree as ET, sys
tree = ET.parse('coverage.xml')
root = tree.getroot()
rate = float(root.attrib.get('line-rate', '0')) * 100
print(f'{rate:.1f}')
" 2>/dev/null || echo "0")

    if python3 -c "import sys; sys.exit(0 if float('$TOTAL_COV') >= float('$COV_DEFAULT') else 1)" 2>/dev/null; then
        pass "coverage: total ${TOTAL_COV}% >= threshold ${COV_DEFAULT}%"
    else
        fail "coverage: total ${TOTAL_COV}% < threshold ${COV_DEFAULT}%"
    fi

    # Per-module coverage check (client, storage, worker)
    for module_info in "client:$COV_CLIENT" "storage:$COV_STORAGE" "worker:$COV_WORKER"; do
        MOD_NAME="${module_info%%:*}"
        MOD_THRESH="${module_info##*:}"
        MOD_COV=$(python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('coverage.xml')
for pkg in tree.iter('package'):
    for cls in pkg.iter('class'):
        fname = cls.attrib.get('filename', '')
        if '${MOD_NAME}.py' in fname:
            rate = float(cls.attrib.get('line-rate', '0')) * 100
            print(f'{rate:.1f}')
            exit()
print('0')
" 2>/dev/null || echo "0")
        if [[ -f "src/pageindex_mcp/${MOD_NAME}.py" ]]; then
            if python3 -c "import sys; sys.exit(0 if float('$MOD_COV') >= float('$MOD_THRESH') else 1)" 2>/dev/null; then
                pass "coverage[${MOD_NAME}]: ${MOD_COV}% >= threshold ${MOD_THRESH}%"
            else
                fail "coverage[${MOD_NAME}]: ${MOD_COV}% < threshold ${MOD_THRESH}%"
            fi
        fi
    done
else
    skip "coverage thresholds (pytest-cov not installed or coverage.xml not generated)"
fi

# ── 2c. Assertion density ─────────────────────────────────────────────────────
# Count assert statements and test functions across tests/; compute mean.
TEST_COUNT=$(grep -r '^def test_\|^    def test_' tests/ 2>/dev/null | wc -l | tr -d ' ')
ASSERT_COUNT=$(grep -r '\bassert\b' tests/ 2>/dev/null | wc -l | tr -d ' ')

if [[ "$TEST_COUNT" -gt 0 ]]; then
    DENSITY=$(python3 -c "print(f'{${ASSERT_COUNT}/${TEST_COUNT}:.2f}')" 2>/dev/null || echo "0")
    if python3 -c "import sys; sys.exit(0 if float('$DENSITY') >= float('$ASSERT_DENSITY') else 1)" 2>/dev/null; then
        pass "assertion density: ${DENSITY} asserts/test >= threshold ${ASSERT_DENSITY}"
    else
        fail "assertion density: ${DENSITY} asserts/test < threshold ${ASSERT_DENSITY} (shallow tests)"
    fi
else
    skip "assertion density (no test functions found in tests/)"
fi

# ── 2d. Layer file existence ──────────────────────────────────────────────────
# Every service/repository file must have a corresponding test file.
if [[ "$LAYER_EXISTENCE" == "true" ]]; then
    LAYER_FILES=("client.py" "storage.py" "cache.py" "worker.py" "converters.py")
    for src_file in "${LAYER_FILES[@]}"; do
        module="${src_file%.py}"
        if [[ -f "src/pageindex_mcp/${src_file}" ]]; then
            if [[ -f "tests/test_${module}.py" ]]; then
                pass "layer test existence: test_${module}.py present"
            else
                fail "layer test existence: src/pageindex_mcp/${src_file} exists but tests/test_${module}.py missing"
            fi
        fi
    done
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
for msg in "${MESSAGES[@]}"; do echo "$msg"; done
echo ""
echo "Gate 2 unit: PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"

if [[ "$FAIL" -gt 0 || "$PYTEST_EXIT" -ne 0 ]]; then
    echo "FAIL gate=unit"
    exit 1
else
    echo "PASS gate=unit"
    exit 0
fi
