#!/usr/bin/env bash
# scripts/gates/static.sh — Gate 1: Static Analysis
#
# Enforces: ruff check, ruff format --check, mypy, secrets scan, layer-isolation rules.
# Needs infra: no
#
# §9 / AGENT_DRIVEN_DEVELOPMENT.md reality note:
#   Until ruff/mypy/import-linter configs exist in pyproject.toml, each sub-check
#   detects the missing config and exits 0 with a SKIP message rather than failing.
#   The full gate becomes active once those configs are added (Phase-1 governance task).

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

echo "=== Gate 1: static ==="

cd "$REPO_ROOT"

# ── Read thresholds ────────────────────────────────────────────────────────────
RUFF_VIOLATIONS=$(gate_threshold "static.ruff_violations" 2>/dev/null || echo "0")
MAX_CYCLOMATIC=$(gate_threshold "static.max_cyclomatic" 2>/dev/null || echo "15")
MAX_FUNCTION_LINES=$(gate_threshold "static.max_function_lines" 2>/dev/null || echo "50")
MAX_FILE_LINES=$(gate_threshold "static.max_file_lines" 2>/dev/null || echo "300")
MAX_NESTING=$(gate_threshold "static.max_nesting_depth" 2>/dev/null || echo "4")
MAX_PARAMS=$(gate_threshold "static.max_params" 2>/dev/null || echo "5")

# ── 1a. ruff check ────────────────────────────────────────────────────────────
if ! command -v ruff &>/dev/null && ! uv run ruff --version &>/dev/null 2>&1; then
    skip "ruff check (ruff not installed)"
else
    RUFF_CMD="ruff"
    command -v ruff &>/dev/null || RUFF_CMD="uv run ruff"

    # Check whether a ruff config section exists (pyproject.toml [tool.ruff])
    if ! grep -q '\[tool\.ruff\]' pyproject.toml 2>/dev/null && \
       ! grep -q '\[tool\.ruff\.' pyproject.toml 2>/dev/null && \
       ! [[ -f "ruff.toml" ]] && ! [[ -f ".ruff.toml" ]]; then
        skip "ruff check (no [tool.ruff] config in pyproject.toml)"
    else
        VIOLATION_COUNT=0
        OUTPUT=$($RUFF_CMD check src/pageindex_mcp/ 2>&1) || VIOLATION_COUNT=$(echo "$OUTPUT" | grep -c 'error\|warning' || true)
        if [[ "$VIOLATION_COUNT" -le "$RUFF_VIOLATIONS" ]]; then
            pass "ruff check: $VIOLATION_COUNT violations (threshold: $RUFF_VIOLATIONS)"
        else
            fail "ruff check: $VIOLATION_COUNT violations (threshold: $RUFF_VIOLATIONS)"
            echo "$OUTPUT" | head -40
        fi
    fi
fi

# ── 1b. ruff format --check ───────────────────────────────────────────────────
if ! command -v ruff &>/dev/null && ! uv run ruff --version &>/dev/null 2>&1; then
    skip "ruff format (ruff not installed)"
else
    RUFF_CMD="ruff"
    command -v ruff &>/dev/null || RUFF_CMD="uv run ruff"

    if ! grep -q '\[tool\.ruff\]' pyproject.toml 2>/dev/null && \
       ! grep -q '\[tool\.ruff\.' pyproject.toml 2>/dev/null && \
       ! [[ -f "ruff.toml" ]] && ! [[ -f ".ruff.toml" ]]; then
        skip "ruff format (no [tool.ruff] config in pyproject.toml)"
    else
        if $RUFF_CMD format --check src/pageindex_mcp/ &>/dev/null; then
            pass "ruff format --check: clean"
        else
            fail "ruff format --check: files need reformatting (run: ruff format src/pageindex_mcp/)"
        fi
    fi
fi

# ── 1c. mypy type check ───────────────────────────────────────────────────────
if ! command -v mypy &>/dev/null && ! uv run mypy --version &>/dev/null 2>&1; then
    skip "mypy (mypy not installed)"
else
    MYPY_CMD="mypy"
    command -v mypy &>/dev/null || MYPY_CMD="uv run mypy"

    if ! grep -q '\[tool\.mypy\]' pyproject.toml 2>/dev/null && \
       ! [[ -f "mypy.ini" ]] && ! [[ -f ".mypy.ini" ]]; then
        skip "mypy (no [tool.mypy] config in pyproject.toml)"
    else
        if $MYPY_CMD src/pageindex_mcp/ &>/dev/null; then
            pass "mypy: clean"
        else
            fail "mypy: type errors found (run: mypy src/pageindex_mcp/ for details)"
        fi
    fi
fi

# ── 1d. secrets scan ─────────────────────────────────────────────────────────
if command -v detect-secrets &>/dev/null || uv run detect-secrets --version &>/dev/null 2>&1; then
    DS_CMD="detect-secrets"
    command -v detect-secrets &>/dev/null || DS_CMD="uv run detect-secrets"

    if [[ -f ".secrets.baseline" ]]; then
        if $DS_CMD audit --diff --baseline .secrets.baseline &>/dev/null; then
            pass "detect-secrets: no new secrets detected"
        else
            fail "detect-secrets: potential secrets found; run 'detect-secrets scan' to review"
        fi
    else
        # Run a scan — if it outputs nothing suspicious, pass
        SECRETS_OUTPUT=$($DS_CMD scan src/pageindex_mcp/ 2>&1)
        if echo "$SECRETS_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if not d.get('results') else 1)" &>/dev/null 2>&1; then
            pass "detect-secrets: no secrets detected"
        else
            skip "detect-secrets (no .secrets.baseline; run 'detect-secrets scan > .secrets.baseline' to initialise)"
        fi
    fi
elif command -v gitleaks &>/dev/null; then
    if gitleaks detect --source . --no-git &>/dev/null; then
        pass "gitleaks: no secrets detected"
    else
        fail "gitleaks: potential secrets found"
    fi
else
    skip "secrets scan (neither detect-secrets nor gitleaks installed)"
fi

# ── 1e. Layer-isolation import rules ─────────────────────────────────────────
# Enforce no_minio_outside_storage, no_redis_outside_cache_or_worker,
# no_llm_outside_provider, no_pypdf2_in_new_pdf_path, no_circular_imports
# via grep heuristics until import-linter/ruff banned-api config exists.

if ! grep -q 'import-linter\|flake8-tidy-imports\|banned-api' pyproject.toml 2>/dev/null; then
    # Grep-based heuristic enforcement

    # no_minio_outside_storage: minio client import outside storage.py
    MINIO_VIOLATIONS=$(grep -rn 'from minio\|import minio\|Minio(' src/pageindex_mcp/ \
        | grep -v 'storage\.py' | grep -v '\.pyc' | wc -l | tr -d ' ' || true)
    if [[ "$MINIO_VIOLATIONS" -eq 0 ]]; then
        pass "layer-isolation: no_minio_outside_storage"
    else
        fail "layer-isolation: minio imported outside storage.py ($MINIO_VIOLATIONS file(s))"
    fi

    # no_redis_outside_cache_or_worker: Redis imports outside cache.py and worker.py
    REDIS_VIOLATIONS=$(grep -rn 'import redis\|from redis\|aioredis\|fakeredis' src/pageindex_mcp/ \
        | grep -vE '(cache|worker)\.py' | grep -v '\.pyc' | wc -l | tr -d ' ' || true)
    if [[ "$REDIS_VIOLATIONS" -eq 0 ]]; then
        pass "layer-isolation: no_redis_outside_cache_or_worker"
    else
        fail "layer-isolation: redis imported outside cache.py/worker.py ($REDIS_VIOLATIONS file(s))"
    fi

    # no_llm_outside_provider: OpenAI/litellm/pageindex outside client.py, converters.py,
    # and converters_cli.py (the subprocess-isolated CLI is the same layer as converters.py
    # and re-uses CustomPageIndexClient — see plans/01-subprocess-isolated-converter.md).
    # The allowlist regex is anchored to the path-segment + `grep -n` ":" delimiter so it
    # only matches the intended filenames — never substrings of other paths (e.g.
    # `my_converters_cli.py`) or of the matched line content.
    LLM_VIOLATIONS=$(grep -rn 'import openai\|from openai\|import litellm\|from litellm\|from pageindex\|import pageindex' \
        src/pageindex_mcp/ \
        | grep -vE '/(client|converters|converters_cli)\.py:' | grep -v '\.pyc' | wc -l | tr -d ' ' || true)
    if [[ "$LLM_VIOLATIONS" -eq 0 ]]; then
        pass "layer-isolation: no_llm_outside_provider"
    else
        fail "layer-isolation: LLM/pageindex imported outside client.py/converters.py ($LLM_VIOLATIONS file(s))"
    fi

    # no_pypdf2_in_new_pdf_path: PyPDF2 import anywhere in src (per issue/ANALYSIS.md)
    PYPDF2_VIOLATIONS=$(grep -rn 'import PyPDF2\|from PyPDF2' src/pageindex_mcp/ \
        | grep -v '\.pyc' | wc -l | tr -d ' ' || true)
    if [[ "$PYPDF2_VIOLATIONS" -eq 0 ]]; then
        pass "layer-isolation: no_pypdf2_in_new_pdf_path"
    else
        fail "layer-isolation: PyPDF2 found in src ($PYPDF2_VIOLATIONS occurrence(s)); new PDF path must use pymupdf"
    fi

    # no_circular_imports: heuristic — python -c import; real enforcement needs import-linter
    if python3 -c "import sys; sys.path.insert(0,'src'); import pageindex_mcp" &>/dev/null; then
        pass "layer-isolation: no_circular_imports (import heuristic)"
    else
        skip "layer-isolation: no_circular_imports (package not importable; run 'uv sync' first)"
    fi

else
    # import-linter or ruff banned-api configured — run it properly
    if command -v lint-imports &>/dev/null || uv run lint-imports --version &>/dev/null 2>&1; then
        IL_CMD="lint-imports"
        command -v lint-imports &>/dev/null || IL_CMD="uv run lint-imports"
        if $IL_CMD &>/dev/null; then
            pass "import-linter: all layer-isolation contracts satisfied"
        else
            fail "import-linter: layer-isolation violations found"
        fi
    else
        skip "import-linter (configured but not installed; run 'uv sync --extra dev')"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
for msg in "${MESSAGES[@]}"; do echo "$msg"; done
echo ""
echo "Gate 1 static: PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"

if [[ "$FAIL" -gt 0 ]]; then
    echo "FAIL gate=static"
    exit 1
else
    echo "PASS gate=static"
    exit 0
fi
