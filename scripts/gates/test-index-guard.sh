#!/usr/bin/env bash
# scripts/gates/test-index-guard.sh — Gate: Test Index Guard
#
# Enforces the source-to-test mapping in tests/TEST_INDEX.yaml:
#   CHECK 1: Every src/pageindex_mcp/*.py file has a mapping entry  (FAIL)
#   CHECK 2: Every test file listed in the index actually exists    (FAIL)
#   CHECK 3: Changed source files have at least one mapped test     (FAIL if unmapped)
#   CHECK 4: Changed source files → mapped tests also changed       (WARN)
#   CHECK 5: TEST_INDEX.yaml itself is not stale (orphan detection) (WARN)
#
# Usage:
#   ./scripts/gates/test-index-guard.sh              # check against staged changes
#   ./scripts/gates/test-index-guard.sh --diff=HEAD~1 # check against last commit
#   ./scripts/gates/test-index-guard.sh --diff=main   # check against main branch
#   ./scripts/gates/test-index-guard.sh --full         # full audit (no diff, all checks)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INDEX_FILE="$REPO_ROOT/tests/TEST_INDEX.yaml"
SRC_DIR="src/pageindex_mcp"

PASS=0
FAIL=0
WARN=0
MESSAGES=()

pass()  { PASS=$((PASS+1));  MESSAGES+=("  [PASS] $*"); }
fail()  { FAIL=$((FAIL+1));  MESSAGES+=("  [FAIL] $*"); }
warn()  { WARN=$((WARN+1));  MESSAGES+=("  [WARN] $*"); }

echo "=== Gate: Test Index Guard ==="
cd "$REPO_ROOT"

# ── Parse args ────────────────────────────────────────────────────────────────
DIFF_REF=""
FULL_AUDIT=false
for arg in "$@"; do
  case "$arg" in
    --diff=*) DIFF_REF="${arg#--diff=}" ;;
    --full)   FULL_AUDIT=true ;;
  esac
done

# ── Prerequisite: index file exists ───────────────────────────────────────────
if [[ ! -f "$INDEX_FILE" ]]; then
  fail "tests/TEST_INDEX.yaml not found"
  echo ""
  for msg in "${MESSAGES[@]}"; do echo "$msg"; done
  echo ""
  echo "FAIL gate=test-index-guard (pass=$PASS fail=$FAIL warn=$WARN)"
  exit 1
fi

# ── Parse the YAML index (lightweight — no yq dependency) ────────────────────
# Extracts: source_file -> list of test files
declare -A SOURCE_TO_TESTS

current_source=""
while IFS= read -r line; do
  # Match source file entry: "  client.py:" or "  tools/documents.py:"
  if [[ "$line" =~ ^[[:space:]]{2}([a-zA-Z0-9_/]+\.py):$ ]]; then
    current_source="${BASH_REMATCH[1]}"
    SOURCE_TO_TESTS["$current_source"]=""
  fi
  # Match test file entry: "      - test_client.py"
  if [[ "$line" =~ ^[[:space:]]{6}-[[:space:]]+(test_[a-zA-Z0-9_]+\.py) ]]; then
    if [[ -n "$current_source" ]]; then
      existing="${SOURCE_TO_TESTS[$current_source]:-}"
      if [[ -n "$existing" ]]; then
        SOURCE_TO_TESTS["$current_source"]="$existing ${BASH_REMATCH[1]}"
      else
        SOURCE_TO_TESTS["$current_source"]="${BASH_REMATCH[1]}"
      fi
    fi
  fi
done < "$INDEX_FILE"

# ── CHECK 1: Every source file has a mapping entry ───────────────────────────
echo "--- Check 1: Source file coverage in index ---"
unmapped_count=0
while IFS= read -r src_file; do
  rel_path="${src_file#$SRC_DIR/}"
  if [[ -z "${SOURCE_TO_TESTS[$rel_path]+x}" ]]; then
    fail "source file '$rel_path' has no entry in TEST_INDEX.yaml"
    unmapped_count=$((unmapped_count+1))
  fi
done < <(find "$SRC_DIR" -name "*.py" -not -name "__init__.py" -not -name "__pycache__" | sort)

if [[ "$unmapped_count" -eq 0 ]]; then
  pass "all source files have index entries (${#SOURCE_TO_TESTS[@]} mapped)"
fi

# ── CHECK 2: Every listed test file exists ────────────────────────────────────
echo "--- Check 2: Test file existence ---"
missing_tests=0
for source in "${!SOURCE_TO_TESTS[@]}"; do
  for test_file in ${SOURCE_TO_TESTS[$source]}; do
    if [[ ! -f "tests/$test_file" ]]; then
      fail "index lists 'tests/$test_file' for '$source' but file does not exist"
      missing_tests=$((missing_tests+1))
    fi
  done
done

if [[ "$missing_tests" -eq 0 ]]; then
  pass "all listed test files exist"
fi

# ── CHECK 3 & 4: Diff-based checks ───────────────────────────────────────────
if [[ "$FULL_AUDIT" == "false" ]]; then
  echo "--- Check 3 & 4: Changed source files vs test files ---"

  # Get changed files (--diff-filter=d excludes deleted files — they have no
  # TEST_INDEX entry and checking them is meaningless after a module→package split)
  if [[ -n "$DIFF_REF" ]]; then
    changed_files=$(git diff --diff-filter=d --name-only "$DIFF_REF" 2>/dev/null || true)
  else
    # Default: staged + unstaged changes
    changed_files=$(git diff --diff-filter=d --name-only HEAD 2>/dev/null || git diff --diff-filter=d --name-only --cached 2>/dev/null || true)
    if [[ -z "$changed_files" ]]; then
      changed_files=$(git diff --diff-filter=d --name-only --cached 2>/dev/null || true)
    fi
  fi

  # Filter to source files only
  changed_sources=()
  changed_tests=()
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if [[ "$f" == "$SRC_DIR/"* && "$f" == *.py && "$f" != *"__init__"* ]]; then
      changed_sources+=("${f#$SRC_DIR/}")
    fi
    if [[ "$f" == tests/test_*.py ]]; then
      changed_tests+=("$(basename "$f")")
    fi
  done <<< "$changed_files"

  if [[ ${#changed_sources[@]} -eq 0 ]]; then
    pass "no source files changed — nothing to check"
  else
    for src in "${changed_sources[@]}"; do
      mapped_tests="${SOURCE_TO_TESTS[$src]:-}"

      # CHECK 3: source file must have a mapping
      if [[ -z "${SOURCE_TO_TESTS[$src]+x}" ]]; then
        fail "changed source '$src' has no entry in TEST_INDEX.yaml — add it"
        continue
      fi

      if [[ -z "$mapped_tests" ]]; then
        warn "changed source '$src' has an empty test mapping (tests: [])"
        continue
      fi

      # CHECK 4: at least one mapped test file should also be changed
      any_test_changed=false
      for test_file in $mapped_tests; do
        for ct in "${changed_tests[@]}"; do
          if [[ "$ct" == "$test_file" ]]; then
            any_test_changed=true
            break 2
          fi
        done
      done

      if [[ "$any_test_changed" == "true" ]]; then
        pass "source '$src' changed — mapped test(s) also updated"
      else
        warn "source '$src' changed but none of its mapped test(s) were updated"
      fi
    done
  fi
fi

# ── CHECK 5: Orphaned test files (not in any mapping) ────────────────────────
echo "--- Check 5: Orphan detection ---"
# Collect all test files referenced in the index
all_indexed_tests=""
for source in "${!SOURCE_TO_TESTS[@]}"; do
  all_indexed_tests="$all_indexed_tests ${SOURCE_TO_TESTS[$source]}"
done

orphan_count=0
while IFS= read -r test_path; do
  test_basename="$(basename "$test_path")"
  if ! echo "$all_indexed_tests" | grep -qw "$test_basename"; then
    warn "test file '$test_basename' exists but is not mapped to any source in TEST_INDEX.yaml"
    orphan_count=$((orphan_count+1))
  fi
done < <(find tests -maxdepth 1 -name "test_*.py" | sort)

if [[ "$orphan_count" -eq 0 ]]; then
  pass "no orphaned test files"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
for msg in "${MESSAGES[@]}"; do echo "$msg"; done
echo ""

if [[ "$FAIL" -gt 0 ]]; then
  echo "FAIL gate=test-index-guard (pass=$PASS fail=$FAIL warn=$WARN)"
  exit 1
else
  echo "PASS gate=test-index-guard (pass=$PASS fail=$FAIL warn=$WARN)"
  exit 0
fi
