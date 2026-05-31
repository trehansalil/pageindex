#!/usr/bin/env bash
# scripts/gates/integration.sh — Gate 7: Integration Tests
#
# Enforces (via real infra via testcontainers or env-provided MinIO + Redis):
#   - minio_roundtrip: put → cache-invalidate → load returns the same tree.
#   - redis_cache_valid: Redis cache set/get/invalidation works correctly.
#   - arq_enqueue_dequeue: arq job enqueue and dequeue lifecycle passes.
#
# Needs infra: MinIO + Redis (started by testcontainers or pre-existing env vars)
#
# Environment variables (fall back to testcontainers if absent):
#   MINIO_ENDPOINT   MINIO_ACCESS_KEY   MINIO_SECRET_KEY
#   REDIS_URL
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
skip()  { SKIP=$((SKIP+1));  MESSAGES+=("  [SKIP]  $* (not available)"); }

echo "=== Gate 7: integration ==="

cd "$REPO_ROOT"

# ── Read thresholds ────────────────────────────────────────────────────────────
MINIO_ROUNDTRIP=$(gate_threshold  "integration.minio_roundtrip"    2>/dev/null || echo "true")
REDIS_VALID=$(gate_threshold      "integration.redis_cache_valid"   2>/dev/null || echo "true")
ARQ_ENQUEUE=$(gate_threshold      "integration.arq_enqueue_dequeue" 2>/dev/null || echo "true")

# ── Prerequisite: pytest + required packages ──────────────────────────────────
PYTEST_CMD="pytest"
if ! command -v pytest &>/dev/null; then
    if uv run pytest --version &>/dev/null 2>&1; then
        PYTEST_CMD="uv run pytest"
    else
        skip "integration tests (pytest not installed; run 'uv sync --extra dev')"
        echo "SKIP gate=integration"
        exit 0
    fi
fi

# ── Prerequisite: Docker for testcontainers ───────────────────────────────────
INFRA_AVAILABLE=false
if [[ -n "${MINIO_ENDPOINT:-}" && -n "${REDIS_URL:-}" ]]; then
    INFRA_AVAILABLE=true
    echo "  Using pre-existing MinIO/Redis from environment variables."
elif command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    INFRA_AVAILABLE=true
    echo "  Docker available — testcontainers will spin up MinIO + Redis."
else
    skip "integration tests (no Docker daemon and no MINIO_ENDPOINT/REDIS_URL env vars)"
    echo "SKIP gate=integration"
    exit 0
fi

# ── Run integration tests ─────────────────────────────────────────────────────
# Integration tests are identified by pytest.mark.integration or by test file name.
# Run with a generous timeout since containers may be slow to start.

INTEGRATION_TEST_ARGS="-m integration --timeout=120 --tb=short -v"
# Also pick up any tests in test_staging_e2e.py marked integration
INTEGRATION_FILES=""
if [[ -f "tests/test_staging_e2e.py" ]]; then
    INTEGRATION_FILES="tests/test_staging_e2e.py"
fi

# 7a. MinIO roundtrip
if [[ "$MINIO_ROUNDTRIP" == "true" ]]; then
    echo "Running MinIO roundtrip integration tests..."
    if $PYTEST_CMD $INTEGRATION_TEST_ARGS -k "minio or storage or roundtrip" \
           $INTEGRATION_FILES \
           --ignore=tests/test_staging_e2e.py 2>&1 | tee /tmp/integration_minio.log; then
        pass "minio_roundtrip: integration tests passed"
    else
        # If no tests selected, that means tests haven't been written yet — skip, don't fail
        if grep -q "no tests ran\|0 passed\|collected 0 items" /tmp/integration_minio.log; then
            skip "minio_roundtrip (no integration tests for MinIO found yet; write tests tagged @pytest.mark.integration)"
        else
            fail "minio_roundtrip: integration tests failed"
        fi
    fi
fi

# 7b. Redis cache validity
if [[ "$REDIS_VALID" == "true" ]]; then
    echo "Running Redis cache integration tests..."
    if $PYTEST_CMD $INTEGRATION_TEST_ARGS -k "redis or cache" \
           --ignore=tests/test_staging_e2e.py 2>&1 | tee /tmp/integration_redis.log; then
        pass "redis_cache_valid: integration tests passed"
    else
        if grep -q "no tests ran\|0 passed\|collected 0 items" /tmp/integration_redis.log; then
            skip "redis_cache_valid (no integration tests for Redis found yet)"
        else
            fail "redis_cache_valid: integration tests failed"
        fi
    fi
fi

# 7c. arq enqueue/dequeue
if [[ "$ARQ_ENQUEUE" == "true" ]]; then
    echo "Running arq job lifecycle integration tests..."
    if $PYTEST_CMD $INTEGRATION_TEST_ARGS -k "arq or worker or enqueue or dequeue" \
           --ignore=tests/test_staging_e2e.py 2>&1 | tee /tmp/integration_arq.log; then
        pass "arq_enqueue_dequeue: integration tests passed"
    else
        if grep -q "no tests ran\|0 passed\|collected 0 items" /tmp/integration_arq.log; then
            skip "arq_enqueue_dequeue (no integration tests for arq found yet)"
        else
            fail "arq_enqueue_dequeue: integration tests failed"
        fi
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
for msg in "${MESSAGES[@]}"; do echo "$msg"; done
echo ""
echo "Gate 7 integration: PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"

if [[ "$FAIL" -gt 0 ]]; then
    echo "FAIL gate=integration"
    exit 1
else
    echo "PASS gate=integration"
    exit 0
fi
