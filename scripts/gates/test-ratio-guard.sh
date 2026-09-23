#!/usr/bin/env bash
# scripts/gates/test-ratio-guard.sh — Test file proliferation guard
#
# Enforces:
#   1. Ratio ceiling: count(tests/test_*.py) / count(src/**/*.py excl __init__)
#      must not exceed unit.max_test_file_ratio.
#   2. New-file justification: any test file added relative to merge-base with
#      master must carry an # ALLOW-NEW-TEST-FILE marker in its first 10 lines,
#      and (unless grandfathered) that marker must NAME the topical file it
#      could not join and say why:
#
#        # ALLOW-NEW-TEST-FILE: not tests/test_converters.py because <reason>
#
#      A bare marker is free, and free is how 60 test files happened one RFC
#      wave at a time. Naming the home forces the author to go look for one
#      before making a new one, and gives a reviewer something to disagree with.
#   3. Orphan check (WARN): test files not mapped in TEST_INDEX.yaml.
#
# Needs infra: no
# Reads thresholds from agents/governance/verify-gates.yaml via read-yaml.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIB_DIR="$REPO_ROOT/scripts/lib"
GATES_YAML="$REPO_ROOT/agents/governance/verify-gates.yaml"

# shellcheck source=../lib/read-yaml.sh
source "$LIB_DIR/read-yaml.sh"

PASS=0
FAIL=0
WARN=0
MESSAGES=()

pass()  { PASS=$((PASS+1));  MESSAGES+=("  [PASS]  $*"); }
fail()  { FAIL=$((FAIL+1));  MESSAGES+=("  [FAIL]  $*"); }
warn()  { WARN=$((WARN+1));  MESSAGES+=("  [WARN]  $*"); }

echo "=== Gate: test-ratio-guard ==="

cd "$REPO_ROOT"

# ── Read thresholds ────────────────────────────────────────────────────────────
MAX_RATIO=$(gate_threshold "unit.max_test_file_ratio" 2>/dev/null || echo "0.65")
REQUIRE_MARKER=$(gate_threshold "unit.new_test_file_requires_marker" 2>/dev/null || echo "true")
MARKER_NAMES_HOME=$(gate_threshold "unit.new_test_file_marker_names_home" 2>/dev/null || echo "true")

# ── Grandfathered test files ──────────────────────────────────────────────────
# The 27 test files that existed at the end of the ICR-97 consolidation
# (2026-09-23, branch ICR-97-rfc48-surya-image-fallback). Several of them ARE
# the consolidation targets — they predate the "name the home you could not
# join" rule and, being the homes themselves, could not have named one. They are
# exempt from the strict marker FORM; every file created after this date is not.
#
# This list only ever shrinks. Do not add to it: a new file added today has a
# home to name, which is the entire point of the rule.
GRANDFATHERED_TEST_FILES="
test_bidi.py test_cache.py test_client.py test_config.py test_converters.py
test_density_gate.py test_flat.py test_garble.py test_gates.py
test_helpers_combined.py test_hr3_zdr_egress.py test_image_blocks.py
test_inspector.py test_integration.py test_obs_logging.py test_ocr_fallback.py
test_recovery.py test_registry_backfill.py test_registry.py test_script.py
test_source_invariants.py test_staging_e2e.py test_storage.py
test_triad_golden.py test_upload.py test_verdict.py test_worker.py
"

is_grandfathered() {
    case " $(echo $GRANDFATHERED_TEST_FILES) " in
        *" $1 "*) return 0 ;;
        *)        return 1 ;;
    esac
}

# ── Check 1: ratio ceiling ────────────────────────────────────────────────────
TEST_COUNT=$(find tests -maxdepth 1 -name 'test_*.py' | wc -l | tr -d ' ')
SRC_COUNT=$(find src/pageindex_mcp -name '*.py' ! -name '__init__.py' | wc -l | tr -d ' ')

if [ "$SRC_COUNT" -eq 0 ]; then
    fail "Check 1 (ratio): no source files found under src/pageindex_mcp/"
else
    # bash integer arithmetic: multiply by 100 to avoid float
    RATIO_X100=$(( TEST_COUNT * 100 / SRC_COUNT ))
    MAX_X100=$(echo "$MAX_RATIO" | awk '{printf "%d", $1 * 100}')
    RATIO_DISPLAY=$(echo "$TEST_COUNT $SRC_COUNT" | awk '{printf "%.2f", $1/$2}')

    if [ "$RATIO_X100" -gt "$MAX_X100" ]; then
        fail "Check 1 (ratio): test/src ratio $RATIO_DISPLAY ($TEST_COUNT/$SRC_COUNT) exceeds threshold $MAX_RATIO"
    else
        pass "Check 1 (ratio): test/src ratio $RATIO_DISPLAY ($TEST_COUNT/$SRC_COUNT) within threshold $MAX_RATIO"
    fi
fi

# ── Check 2: new-file justification ───────────────────────────────────────────
if [ "$REQUIRE_MARKER" = "true" ]; then
    MERGE_BASE=$(git merge-base HEAD master 2>/dev/null || echo "")
    if [ -n "$MERGE_BASE" ]; then
        NEW_FILES=$(git diff --name-only --diff-filter=A "$MERGE_BASE" -- 'tests/test_*.py' 2>/dev/null || echo "")
        UNJUSTIFIED=()
        VAGUE=()
        for f in $NEW_FILES; do
            [ -f "$f" ] || continue
            MARKER=$(head -10 "$f" | grep -m1 '# ALLOW-NEW-TEST-FILE' || true)
            if [ -z "$MARKER" ]; then
                UNJUSTIFIED+=("$f")
                continue
            fi
            [ "$MARKER_NAMES_HOME" = "true" ] || continue
            is_grandfathered "$(basename "$f")" && continue
            # Required form: names a concrete tests/<file>.py it could not join,
            # then "because" + a reason with actual words in it.
            if ! printf '%s' "$MARKER" \
                 | grep -Eq '# ALLOW-NEW-TEST-FILE:[[:space:]]+not[[:space:]]+tests/[A-Za-z0-9_]+\.py[[:space:]]+because[[:space:]]+[^[:space:]]+([[:space:]]+[^[:space:]]+){2,}'; then
                VAGUE+=("$f")
            fi
        done
        if [ ${#UNJUSTIFIED[@]} -gt 0 ]; then
            fail "Check 2 (new-file): ${#UNJUSTIFIED[@]} new test file(s) without # ALLOW-NEW-TEST-FILE marker: ${UNJUSTIFIED[*]}"
        elif [ ${#VAGUE[@]} -gt 0 ]; then
            fail "Check 2 (new-file): ${#VAGUE[@]} new test file(s) carry a marker that does not name the home it could not join: ${VAGUE[*]}
            Required form (first 10 lines of the file):
              # ALLOW-NEW-TEST-FILE: not tests/test_converters.py because <reason, >=3 words>
            Name the topical file you considered and rejected. If no such file
            exists, say which one you would have extended and why it is the
            wrong home. A bare marker is not a justification."
        else
            pass "Check 2 (new-file): all new test files justified or none added"
        fi
    else
        pass "Check 2 (new-file): skipped (no merge-base with master — ratio-only mode)"
    fi
else
    pass "Check 2 (new-file): marker requirement disabled"
fi

# ── Check 3: orphan check (WARN only) ─────────────────────────────────────────
INDEX_FILE="$REPO_ROOT/tests/TEST_INDEX.yaml"
if [ -f "$INDEX_FILE" ]; then
    ORPHAN_COUNT=0
    for f in tests/test_*.py; do
        BASENAME=$(basename "$f")
        if ! grep -q "$BASENAME" "$INDEX_FILE"; then
            ORPHAN_COUNT=$((ORPHAN_COUNT + 1))
        fi
    done
    if [ "$ORPHAN_COUNT" -gt 0 ]; then
        warn "Check 3 (orphan): $ORPHAN_COUNT test file(s) not found in TEST_INDEX.yaml"
    else
        pass "Check 3 (orphan): all test files mapped in TEST_INDEX.yaml"
    fi
else
    warn "Check 3 (orphan): TEST_INDEX.yaml not found"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
for msg in "${MESSAGES[@]}"; do
    echo "$msg"
done
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "FAIL gate=test-ratio-guard  ($PASS passed, $FAIL failed, $WARN warnings)"
    exit 1
else
    echo "PASS gate=test-ratio-guard  ($PASS passed, $FAIL failed, $WARN warnings)"
    exit 0
fi
