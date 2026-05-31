#!/usr/bin/env bash
# scripts/gates/build.sh — Gate 5: Build (wheel; Docker image is opt-in)
#
# Enforces (fast no-infra prefix — what eval.sh runs):
#   - uv build produces a wheel without error, within max_time_seconds.
#
# Opt-in (--with-docker; NOT passed by eval.sh):
#   - docker build . succeeds within max_time_seconds.
#   - Final Docker image size does not exceed max_image_mb.
#   The docker image build + push is OWNED by the build-push CI job
#   (docker/build-push-action), which builds+pushes the real image and fails the
#   workflow — blocking the deploy — on any Dockerfile breakage. The
#   Docling-default image (torch-CPU + baked layout/TableFormer weights +
#   LibreOffice, per RFC-003 Amend.4) is ~3GB and takes ~60-120s cold, which does
#   not fit the "fast" no-infra prefix budget, so it is decoupled from this gate.
#
# Needs infra: no
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

echo "=== Gate 5: build ==="

cd "$REPO_ROOT"

# ── Read thresholds ────────────────────────────────────────────────────────────
MAX_TIME=$(gate_threshold    "build.max_time_seconds" 2>/dev/null || echo "60")
MAX_IMAGE_MB=$(gate_threshold "build.max_image_mb"    2>/dev/null || echo "1500")

# ── Parse arguments ────────────────────────────────────────────────────────────
# Docker build is opt-in: the fast no-infra gate validates only the wheel; the
# docker image build + push is owned by the build-push CI job (see 5b below).
WITH_DOCKER=false
for arg in "$@"; do
    case "$arg" in
        --with-docker) WITH_DOCKER=true ;;
    esac
done

# ── 5a. uv build (wheel) ─────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
    fail "uv build: uv not installed (required build prerequisite — gate cannot verify the wheel)"
else
    BUILD_START=$(date +%s)
    if uv build --out-dir /tmp/pageindex_build_output &>/tmp/uv_build.log; then
        BUILD_END=$(date +%s)
        BUILD_ELAPSED=$((BUILD_END - BUILD_START))
        if [[ "$BUILD_ELAPSED" -le "$MAX_TIME" ]]; then
            pass "uv build: wheel produced in ${BUILD_ELAPSED}s (threshold: ${MAX_TIME}s)"
        else
            fail "uv build: succeeded but took ${BUILD_ELAPSED}s (threshold: ${MAX_TIME}s)"
        fi
        # Clean up build output
        rm -rf /tmp/pageindex_build_output
    else
        fail "uv build: failed (see /tmp/uv_build.log)"
        cat /tmp/uv_build.log | tail -20
    fi
fi

# ── 5b. docker build (opt-in; delegated to build-push CI job by default) ──────
if [[ "$WITH_DOCKER" != "true" ]]; then
    SKIP=$((SKIP+1))
    MESSAGES+=("  [SKIP]  docker build + image size: delegated to the build-push CI job (not run in the fast no-infra gate; pass --with-docker to enable locally)")
elif ! command -v docker &>/dev/null; then
    fail "docker build: docker not installed (required prerequisite — gate cannot verify the image)"
elif ! docker info &>/dev/null 2>&1; then
    fail "docker build: Docker daemon not running (required prerequisite — gate cannot verify the image)"
elif [[ ! -f "Dockerfile" ]]; then
    fail "docker build: Dockerfile not found in $REPO_ROOT"
else
    IMAGE_TAG="pageindex-mcp-gate-check:$(git rev-parse --short HEAD 2>/dev/null || echo 'local')"
    DOCKER_START=$(date +%s)

    if docker build -t "$IMAGE_TAG" . &>/tmp/docker_build.log; then
        DOCKER_END=$(date +%s)
        DOCKER_ELAPSED=$((DOCKER_END - DOCKER_START))

        if [[ "$DOCKER_ELAPSED" -le "$MAX_TIME" ]]; then
            pass "docker build: image built in ${DOCKER_ELAPSED}s (threshold: ${MAX_TIME}s)"
        else
            fail "docker build: succeeded but took ${DOCKER_ELAPSED}s (threshold: ${MAX_TIME}s)"
        fi

        # Check image size
        IMAGE_SIZE_BYTES=$(docker image inspect "$IMAGE_TAG" \
            --format '{{.Size}}' 2>/dev/null || echo "0")
        IMAGE_SIZE_MB=$(( IMAGE_SIZE_BYTES / 1048576 ))

        if [[ "$IMAGE_SIZE_MB" -le "$MAX_IMAGE_MB" ]]; then
            pass "docker image size: ${IMAGE_SIZE_MB}MB (threshold: ${MAX_IMAGE_MB}MB)"
        else
            fail "docker image size: ${IMAGE_SIZE_MB}MB exceeds threshold ${MAX_IMAGE_MB}MB"
        fi

        # Clean up temporary build image to avoid disk accumulation
        docker rmi "$IMAGE_TAG" &>/dev/null || true
    else
        fail "docker build: failed (see /tmp/docker_build.log)"
        tail -30 /tmp/docker_build.log
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
for msg in "${MESSAGES[@]}"; do echo "$msg"; done
echo ""
echo "Gate 5 build: PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"

if [[ "$FAIL" -gt 0 ]]; then
    echo "FAIL gate=build"
    exit 1
else
    echo "PASS gate=build"
    exit 0
fi
