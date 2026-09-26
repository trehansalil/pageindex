#!/usr/bin/env bash
# Mac docling-service auto-updater (RFC-052 task 1.10). launchd runs this every
# 300 s (com.pageindex.docling-updater, installed by install.sh). It
# fast-forwards the checkout to origin/master and re-runs install.sh (deps,
# run.sh, service restart) -- but only when ALL of these hold:
#   - origin/master changed something the service runs: services/docling-service/,
#     src/, pyproject.toml or uv.lock (or the last install did not complete);
#   - the service is idle: /health reports in_flight == 0, or does not answer;
#   - the checkout is on master, HEAD is an ancestor of origin/master, and no
#     tracked file is modified.
# Never forces, resets or discards anything. Every failure is logged to
# logs/updater.log and exits 0: launchd simply retries on the next interval.
set -uo pipefail

ROOT="${PAGEINDEX_ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
INSTALL_SH="${PAGEINDEX_INSTALL_SH:-$ROOT/services/docling-service/macos/install.sh}"
BRANCH="${PAGEINDEX_UPDATE_BRANCH:-master}"
PORT="${DOCLING_PORT:-8090}"
WATCHED=(services/docling-service src pyproject.toml uv.lock)
LOG="$ROOT/logs/updater.log"
LOCK="$ROOT/logs/.updater.lock"
STAMP="$ROOT/logs/.installed-rev"  # written by install.sh on a healthy start

mkdir -p "$ROOT/logs" || exit 0
cd "$ROOT" || exit 0
# Nothing else rotates this log.
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 1048576 ]; then mv -f "$LOG" "$LOG.1"; fi
exec >>"$LOG" 2>&1

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# mkdir is atomic, so two runs never overlap. A lock older than 2 h belongs to
# a run that died (reboot mid-install); clear it rather than wedge forever.
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    log "clearing stale lock $LOCK"
    rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null || exit 0
  else
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { log "not a git checkout; nothing to do"; exit 0; }
branch="$(git symbolic-ref --quiet --short HEAD)" || { log "detached HEAD; not updating"; exit 0; }
[ "$branch" = "$BRANCH" ] || { log "on branch $branch, not $BRANCH; not updating"; exit 0; }
git fetch --quiet origin "$BRANCH" || { log "git fetch origin $BRANCH failed"; exit 0; }

head="$(git rev-parse HEAD)" || exit 0
remote="$(git rev-parse --verify --quiet "origin/$BRANCH")" || { log "no origin/$BRANCH"; exit 0; }
installed="$(cat "$STAMP" 2>/dev/null || true)"

merge=0
if [ "$remote" != "$head" ] && ! git diff --quiet "$head" "$remote" -- "${WATCHED[@]}"; then
  merge=1
fi
# Up to date and the last install completed: the common, silent case.
[ "$merge" = 0 ] && [ "$installed" = "$head" ] && exit 0

if [ "$merge" = 1 ]; then
  git merge-base --is-ancestor "$head" "$remote" \
    || { log "HEAD ${head:0:12} is not an ancestor of origin/$BRANCH (local commits?); not updating"; exit 0; }
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    log "tracked files modified locally; not updating: $(git status --porcelain --untracked-files=no | tr '\n' ' ')"
    exit 0
  fi
fi

# Idle check. The service answering without in_flight is an older build: do
# not restart it blind. Not answering at all means nothing is in flight.
ip="$(tailscale ip -4 2>/dev/null | head -1)"
health="$(curl -fsS -m 5 "http://${ip:-127.0.0.1}:$PORT/health" 2>/dev/null || true)"
if [ -n "$health" ]; then
  in_flight="$(printf '%s' "$health" | sed -n 's/.*"in_flight":[[:space:]]*\([0-9][0-9]*\).*/\1/p')"
  [ -n "$in_flight" ] || { log "/health has no in_flight field; not restarting blind"; exit 0; }
  [ "$in_flight" = 0 ] || { log "busy (in_flight=$in_flight); retrying next interval"; exit 0; }
else
  log "service not answering /health; proceeding (nothing in flight)"
fi

if [ "$merge" = 1 ]; then
  git merge --ff-only --quiet "origin/$BRANCH" || { log "fast-forward to origin/$BRANCH failed"; exit 0; }
  log "fast-forwarded ${head:0:12} -> ${remote:0:12}"
  head="$remote"
else
  log "last install did not complete for ${head:0:12}; re-running install.sh"
fi

if PAGEINDEX_FROM_UPDATER=1 "$INSTALL_SH"; then
  log "installed ${head:0:12}"
else
  log "install.sh failed for ${head:0:12}; retrying next interval"
fi
exit 0
