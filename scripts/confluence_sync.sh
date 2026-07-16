#!/usr/bin/env bash
# Push .agents/{rfcs,designs,tasks} markdown to Confluence via the `mark` CLI.
#
# WHY: RFCs/designs/task-plans live as markdown in-repo but need a Confluence
# mirror for non-engineering stakeholders. `mark` is opt-in per-file (it only
# touches files carrying a `<!-- Space: -->` header) and writes the assigned
# Confluence-Page-ID back into the source file on first push, so re-running
# this script is always safe.
#
# Usage:
#   scripts/confluence_sync.sh            # scaffold + push changed/new docs
#   scripts/confluence_sync.sh --dry-run  # scaffold + show what would push, no write
#
# Required env (see .env.example):
#   CONFLUENCE_URL, CONFLUENCE_USER, CONFLUENCE_API_TOKEN
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS_DIR="$ROOT_DIR/.agents"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

if ! command -v mark >/dev/null 2>&1; then
  echo "ERROR: mark CLI not found. Install: brew install mark (or go install github.com/kovetskiy/mark@latest)" >&2
  exit 1
fi

# Load CONFLUENCE_* from .env if not already present in the environment, so
# this script works standalone (no need to `export` or manually source .env
# before every `make confluence-sync`).
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

: "${CONFLUENCE_URL:?missing — set in .env or environment}"
: "${CONFLUENCE_USER:?missing — set in .env or environment}"
: "${CONFLUENCE_API_TOKEN:?missing — set in .env or environment}"

echo "==> Ensuring mark headers + RFC/design/tasks companions exist"
python3 "$ROOT_DIR/scripts/confluence_scaffold.py"

# Invoke mark once per file (rather than repeating --files across one mark
# call) because mark's --files is a plain string flag: repeating it just
# overwrites the previous value, so only the LAST file passed ever gets
# synced — the rest are silently dropped. Confirmed against mark 16.5.0.
# We also skip the folder-root `*-metadata.md` pointer files: those only
# carry Space/Folder/Confluence-Page-ID for the RFCs/Designs/Tasks root page
# itself, have no Title header, and mark FATALs on them ("doesn't contain
# metadata"). Expanding here (after the scaffold step above already ran)
# still picks up any file the scaffold just created.
#
# --parents is the anchor page RFCs/Designs/Tasks live under in Confluence.
# Without it, mark's per-file `Folder: RFCs|Designs|Tasks` header resolves as
# a top-level folder and FATALs ("cannot create top-level folder ... without
# a MARK_PARENTS anchor page") instead of finding the existing nested folder.
MARK_PARENT_PAGE="Data-AI Refactoring Experiments"

BASE_ARGS=(
  --base-url "$CONFLUENCE_URL"
  --username "$CONFLUENCE_USER"
  --password "$CONFLUENCE_API_TOKEN"
  --changes-only
  --parents "$MARK_PARENT_PAGE"
)
if [[ "$DRY_RUN" == "1" ]]; then
  BASE_ARGS+=(--dry-run)
  echo "==> Dry run: resolving pages, no content will be pushed"
else
  echo "==> Syncing to Confluence"
fi

FILES=()
for dir in rfcs designs tasks; do
  for f in "$AGENTS_DIR/$dir"/*.md; do
    [[ "$(basename "$f")" == *-metadata.md ]] && continue
    FILES+=("$f")
  done
done

# Each mark call only reads/writes its own file and targets its own
# Confluence page, so calls are independent and safe to run concurrently.
# MARK_CONCURRENCY lets callers tune this against Confluence API rate limits.
CONCURRENCY="${MARK_CONCURRENCY:-4}"
printf '%s\n' "${FILES[@]}" | xargs -I{} -P "$CONCURRENCY" mark "${BASE_ARGS[@]}" --files {}
