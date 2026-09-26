#!/usr/bin/env bash
# Ship the Mac docling-service's logs to the cluster Loki (RFC-052 R1 AC9,
# task 1.10): Grafana Alloy as a launchd agent tailing logs/service.log, and
# newsyslog rotating it at 10 MB x 5. Idempotent; re-run to update.
#
# OPERATOR STEP -- run on the Mac, from the unpacked bundle, AFTER install.sh
# (which makes the service write JSON to logs/service.log):
#   LOKI_TAILSCALE_NODEPORT=<port> ./services/docling-service/macos/install-logging.sh
# The NodePort is the loki-tailscale Service's (task 1.9, infra repo):
#   kubectl -n infra get svc loki-tailscale -o jsonpath='{.spec.ports[0].nodePort}'
# Writing /etc/newsyslog.d needs sudo; you are prompted once.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HERE="$ROOT/services/docling-service/macos"
LABEL=com.pageindex.alloy
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$ROOT/logs"
SERVICE_LOG="$LOG_DIR/service.log"
STORAGE="$HOME/Library/Application Support/pageindex-alloy"
NEWSYSLOG_CONF=/etc/newsyslog.d/docling.conf
LOKI_HOST=100.120.146.20  # portfolio's tailscale0 address

NODEPORT="${LOKI_TAILSCALE_NODEPORT:-${1:-}}"
[[ "$NODEPORT" =~ ^[0-9]+$ ]] || {
  echo "set LOKI_TAILSCALE_NODEPORT (the loki-tailscale NodePort), e.g." >&2
  echo "  LOKI_TAILSCALE_NODEPORT=31100 $0" >&2
  exit 1
}

for tool in brew tailscale; do
  command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }
done
command -v alloy >/dev/null || brew install grafana/grafana/alloy
ALLOY_BIN="$(command -v alloy)"

# brew's own `alloy` service reads a different config; two agents would ship
# every line twice.
if brew services list 2>/dev/null | awk '$1=="alloy" && $2=="started"' | grep -q .; then
  echo "stopping brew's alloy service (this agent replaces it)"
  brew services stop alloy
fi

mkdir -p "$LOG_DIR" "$STORAGE"
touch "$SERVICE_LOG"
"$ALLOY_BIN" fmt "$HERE/alloy/config.alloy" >/dev/null  # syntax check

# The push target must answer over Tailscale before we rely on it. Loki's
# /ready is on the same port as the push API.
if ! curl -fsS -m 5 "http://$LOKI_HOST:$NODEPORT/ready" >/dev/null 2>&1; then
  echo "warning: http://$LOKI_HOST:$NODEPORT/ready not reachable over Tailscale;" \
       "alloy will retry, but check task 1.9 (NodePort + tailscale0 rule)" >&2
fi

render() {  # render <template>  -- substitute the @PLACEHOLDERS@
  sed -e "s|@ALLOY_BIN@|$ALLOY_BIN|g" \
      -e "s|@CONFIG@|$HERE/alloy/config.alloy|g" \
      -e "s|@STORAGE@|$STORAGE|g" \
      -e "s|@SERVICE_LOG@|$SERVICE_LOG|g" \
      -e "s|@NODEPORT@|$NODEPORT|g" \
      -e "s|@LOG_DIR@|$LOG_DIR|g" \
      -e "s|@OWNER@|$(id -un):$(id -gn)|g" \
      "$1"
}

# newsyslog: rewrite only when it changed, so a re-run does not re-prompt.
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
render "$HERE/newsyslog/docling.conf.template" >"$tmp"
if ! cmp -s "$tmp" "$NEWSYSLOG_CONF" 2>/dev/null; then
  sudo install -m 644 -o root -g wheel "$tmp" "$NEWSYSLOG_CONF"
  echo "installed $NEWSYSLOG_CONF"
fi
sudo newsyslog -nvf "$NEWSYSLOG_CONF" >/dev/null  # dry run: parses the file

render "$HERE/launchd/com.pageindex.alloy.plist.template" >"$PLIST"
plutil -lint "$PLIST" >/dev/null

# Same bootout/bootstrap dance as install.sh: bootout returns before the old
# agent is gone, and bootstrap fails with EIO until it is.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
for _ in $(seq 20); do
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
  sleep 1
done
launchctl bootstrap "gui/$(id -u)" "$PLIST"

for _ in $(seq 20); do
  if curl -fsS -m 2 http://127.0.0.1:12345/-/ready >/dev/null 2>&1; then
    echo "alloy up; tailing $SERVICE_LOG -> http://$LOKI_HOST:$NODEPORT"
    echo "verify in Grafana: {host=\"mac\", service=\"docling-service\"}"
    exit 0
  fi
  sleep 3
done
echo "alloy not ready after 60 s; see $LOG_DIR/alloy.log" >&2
exit 1
