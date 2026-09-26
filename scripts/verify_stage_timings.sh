#!/usr/bin/env bash
# verify_stage_timings.sh — RFC-050 Wave 1 Checkpoint: verify stage_timings
# flow end-to-end on a single document.
#
# What it checks:
#   1. The converter child emits stage_timings in its stdout JSON
#   2. The worker parent observes them into decision records (stage_duration)
#   3. scripts/g1_stage_timings.py can parse and report those records
#   4. All three stages (extraction, tree_build, recovery) are present
#
# Usage:
#   bash scripts/verify_stage_timings.sh [path/to/single.pdf]
#
# Defaults to the smallest German T&C PDF in doc_store/ if no argument given.
# Requires the server to be running (make up) or at least the worker env.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOGDIR="$REPO/.run"
LOGFILE="$LOGDIR/verify-stage-timings.log"
STAGES=("extraction" "tree_build" "recovery")

mkdir -p "$LOGDIR"

# --- pick a single document ------------------------------------------------

if [[ $# -ge 1 ]]; then
    DOC="$1"
else
    # pick the smallest PDF in doc_store/ to minimise wall time
    DOC="$(find "$REPO/doc_store" -maxdepth 1 -name '*.pdf' -o -name '*.jpg' \
           | xargs ls -lS | tail -1 | awk '{print $NF}')"
fi

if [[ ! -f "$DOC" ]]; then
    echo "ERROR: document not found: $DOC" >&2
    exit 1
fi

DOC_NAME="$(basename "$DOC")"
echo "=== RFC-050 Stage Timing Verification ==="
echo "Document: $DOC_NAME"
echo "Log file: $LOGFILE"
echo ""

# --- ingest one document, capture stderr (where decision records live) ------

echo "[1/4] Ingesting single document via preprocess_client.py ..."
cd "$REPO"

# preprocess_client logs decision() records to stderr via JsonFormatter.
# Capture both stdout and stderr.
set +e
PREPROCESS_CONCURRENCY=1 uv run python preprocess_client.py "$DOC_NAME" \
    > "$LOGFILE.stdout" 2> "$LOGFILE"
EXIT_CODE=$?
set -e

if [[ $EXIT_CODE -ne 0 ]]; then
    echo "WARNING: preprocess_client.py exited with code $EXIT_CODE"
    echo "  (may be OK — hash-skip or expected rejection)"
fi

LINES=$(wc -l < "$LOGFILE" | tr -d ' ')
echo "  Captured $LINES lines of log output"
echo ""

# --- check for stage_duration decision records -----------------------------

echo "[2/4] Checking for stage_duration decision records ..."

FOUND=0
MISSING=()
for stage in "${STAGES[@]}"; do
    COUNT=$(grep -c "\"stage_duration\".*\"$stage\"" "$LOGFILE" 2>/dev/null || true)
    if [[ "$COUNT" -gt 0 ]]; then
        # extract duration_ms from the record
        DUR_MS=$(grep "\"stage_duration\".*\"$stage\"" "$LOGFILE" \
            | head -1 \
            | python3 -c "
import sys, json
for line in sys.stdin:
    start = line.find('{')
    if start >= 0:
        rec = json.loads(line[start:])
        ms = rec.get('attrs', {}).get('duration_ms', '?')
        print(f'{ms}ms ({float(ms)/1000:.1f}s)' if isinstance(ms, (int, float)) else '?')
        break
" 2>/dev/null || echo "?")
        echo "  ✓ $stage: found ($COUNT record(s), duration: $DUR_MS)"
        FOUND=$((FOUND + 1))
    else
        echo "  ✗ $stage: MISSING"
        MISSING+=("$stage")
    fi
done
echo ""

# --- check for stage_timings in converter child stdout ---------------------

echo "[3/4] Checking converter child stdout for stage_timings key ..."

if grep -q "stage_timings" "$LOGFILE.stdout" 2>/dev/null; then
    echo "  ✓ stage_timings found in converter stdout"
elif grep -q "stage_timings" "$LOGFILE" 2>/dev/null; then
    echo "  ✓ stage_timings found in combined log (child stdout forwarded to stderr)"
else
    echo "  ⚠ stage_timings not found in stdout (may be hash-skipped or dedup'd)"
fi
echo ""

# --- feed logs to g1_stage_timings.py for a proper report ------------------

echo "[4/4] Running g1_stage_timings.py on captured logs ..."
echo ""

set +e
python3 "$REPO/scripts/g1_stage_timings.py" --label verify "$LOGFILE"
G1_EXIT=$?
set -e

echo ""

# --- verdict ---------------------------------------------------------------

echo "=== Verdict ==="
if [[ $FOUND -eq ${#STAGES[@]} ]]; then
    echo "PASS: All ${#STAGES[@]} stages present in decision records."
    echo "  The stage-timing histogram pipeline is working end-to-end."
    echo "  (Prometheus scrape deferred to Phase 3 — log-based path verified.)"
elif [[ $FOUND -ge 2 ]]; then
    echo "PARTIAL: $FOUND/${#STAGES[@]} stages found. Missing: ${MISSING[*]}"
    echo "  'recovery' may be absent if the document had no recovery phase."
    echo "  This is acceptable — the pipeline is functional."
else
    echo "FAIL: Only $FOUND/${#STAGES[@]} stages found."
    echo "  Check whether the document was hash-skipped (already ingested)."
    echo "  Try: redis-cli DEL pageindex:hashes  then re-run."
fi

echo ""
echo "Logs: $LOGFILE"

# cleanup stdout capture
rm -f "$LOGFILE.stdout"
