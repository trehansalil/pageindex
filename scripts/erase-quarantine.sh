#!/usr/bin/env bash
# erase-quarantine.sh — remove quarantine objects for a given sha256.
# Usage: scripts/erase-quarantine.sh <sha256>
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <sha256>" >&2
  exit 1
fi

SHA256="$1"

if [[ ! "$SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "ERROR: argument must be a 64-character lowercase hex sha256 digest" >&2
  exit 2
fi

# The digest is passed as an argv element, never interpolated into the Python
# source, so a hostile value cannot terminate the string literal and execute
# code under the operator's account.
exec uv run python -c '
import sys

from pageindex_mcp.storage.documents import erase_quarantine

sha256 = sys.argv[1]
errors = erase_quarantine(sha256)
if errors:
    for e in errors:
        print(f"ERROR: {e}")
    raise SystemExit(1)
print(f"OK: quarantine erased for {sha256}")
' "$SHA256"
