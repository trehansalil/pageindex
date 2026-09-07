#!/usr/bin/env bash
# Unblocks the two fixes the agent session could not apply itself.
#
#   1. .claude/ is owned by uid 501 (rsync -a mode leak from the dev Mac) and this
#      container's root has an EMPTY effective capability set (capsh --print -> "Current: ="),
#      so it has no CAP_DAC_OVERRIDE and cannot write uid-501 files.
#   2. /root/.claude.json sits on a read-only mount in this session.
#
# Run this from a shell that can write both. Idempotent.
set -euo pipefail
REPO="${REPO:-/mnt/HC_Volume_106759881/pageindex_deployment}"

echo "==> 1/3 normalising .claude/ ownership + modes"
chown -R root:root "$REPO/.claude"
find "$REPO/.claude" -type d -exec chmod 755 {} +
find "$REPO/.claude" -type f -exec chmod 644 {} +

echo "==> 2/3 applying workflow-script fixes"
git -C "$REPO" apply --verbose "$REPO/audit/dead-code-workflow-fixes.patch"

echo "==> 3/3 registering codebase-memory-mcp + obsidian for this project path"
python3 - <<'PY'
import json, shutil
p = '/root/.claude.json'
shutil.copy(p, '/root/.claude/claude.json.bak-mcpproj')
d = json.load(open(p))
src = d['projects']['/root/pageindex_deployment']['mcpServers']
dst = d['projects'].setdefault('/mnt/HC_Volume_106759881/pageindex_deployment', {})
dst.setdefault('mcpServers', {}).update(src)
json.dump(d, open(p, 'w'), indent=2)
print('registered:', list(dst['mcpServers']))
PY

echo "==> done. Restart Claude Code (or /mcp reconnect) so the new servers are picked up."
