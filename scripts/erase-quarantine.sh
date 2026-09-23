#!/usr/bin/env bash
# erase-quarantine.sh — remove quarantine objects for a given sha256.
# Usage: scripts/erase-quarantine.sh <sha256>
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <sha256>" >&2
  exit 1
fi

SHA256="$1"

exec uv run python -c "
from pageindex_mcp.storage.documents import erase_quarantine
errors = erase_quarantine('${SHA256}')
if errors:
    for e in errors:
        print(f'ERROR: {e}')
    raise SystemExit(1)
print('OK: quarantine erased for ${SHA256}')
"
