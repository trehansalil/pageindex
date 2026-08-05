#!/usr/bin/env bash
# env_profile.sh — resolve the local/remote toggles into a single .env.active
# that the server, the worker, docker compose, and scripts/remote_ingest_test.py
# all read.
#
#   ./scripts/env_profile.sh              # write .env.active
#   ./scripts/env_profile.sh --show       # resolve and print, write nothing
#   PI_MINIO=local ./scripts/env_profile.sh
#
# ─── The toggles ─────────────────────────────────────────────────────────────
#
#   PI_PROFILE       remote | local | hybrid      (default: remote)
#                      remote — everything against the shared cluster
#                      local  — everything against docker-compose
#                      hybrid — compose infra, but the Scaleway Docling service
#   PI_MINIO         remote | local               (default: from PI_PROFILE)
#   PI_REDIS         remote | local               (default: from PI_PROFILE)
#   PI_POSTGRES      remote | local               (default: from PI_PROFILE)
#   PI_DOCLING       remote | local               (default: from PI_PROFILE —
#                                                  local only under PROFILE=local)
#   PI_APP           host | compose               (default: host)
#   PI_MINIO_ACCESS  auto | cluster | public      (default: auto)
#
# PI_MINIO_ACCESS is the "works from anywhere" knob. The remote MinIO has two
# addresses: an in-cluster ClusterIP (fast, but only routable on the k3s node)
# and a public HTTPS host (routable from a laptop). `auto` probes the ClusterIP
# and falls back to the public host, so the same command works in both places.
#
# Set toggles per-invocation, or persist them in env/profile.env (gitignored).
set -euo pipefail

cd "$(dirname "$0")/.."

BASE_ENV="${BASE_ENV:-.env}"
LOCAL_ENV="${LOCAL_ENV:-env/local.env}"
REMOTE_ENV="${REMOTE_ENV:-env/remote.env}"
PROFILE_ENV="${PROFILE_ENV:-env/profile.env}"
OUT="${OUT:-.env.active}"

SHOW_ONLY=0
[ "${1:-}" = "--show" ] && SHOW_ONLY=1

WARNINGS=()
warn() { WARNINGS+=("$1"); }

# Serialize NAME=value for a file with three consumers: `.`-sourced by make
# targets, `docker compose --env-file`, and python-dotenv. Single quotes come
# closest to a common form — none of the three give $, `, ", or whitespace any
# meaning inside them, whereas double quotes diverge (a shell unescapes \$ and
# \`, dotenv leaves them literal) and unquoted truncates on the first space.
#
# But single quotes are not a *universal* form. Measured, not assumed:
#
#   value   `.`-sourced   python-dotenv
#   a\b     a\b           a\b
#   a\\b    a\\b          a\b            <- dotenv unescapes \\, the shell does not
#   ab\\    ab\\          unparseable line
#   p'ss    p'ss          unparseable line   (via the `'\''` splice)
#
# So a value holding ' or \ cannot be written such that every consumer reads
# the same bytes back, and the Python side of that divergence is silent: the
# worker just gets a different secret and MinIO/Docling answer 403 a long way
# from here. Reject at generation time, where the cause is still named.
emit() {
  local v="${2-}"
  case "$v" in
    *\'*|*\\*)
      echo "error: $1 contains a single quote or backslash. No quoting form is" \
           "read identically by '.'-sourcing, 'docker compose --env-file', and" \
           "python-dotenv, so this value cannot be written to $OUT without one" \
           "consumer silently seeing a different secret. Rotate the credential" \
           "to drop ' and \\." >&2
      exit 2 ;;
  esac
  printf "%s='%s'\n" "$1" "$v"
}

# ─── Load the layers ─────────────────────────────────────────────────────────
# Persisted toggles load first so a per-invocation env var still wins.
if [ -f "$PROFILE_ENV" ]; then
  while IFS='=' read -r k v; do
    case "$k" in PI_*) [ -z "${!k:-}" ] && export "$k=$v" ;; esac
  done < <(grep -E '^PI_[A-Z_]+=' "$PROFILE_ENV" || true)
fi

[ -f "$BASE_ENV" ] || { echo "base env file '$BASE_ENV' not found" >&2; exit 1; }
[ -f "$LOCAL_ENV" ] || { echo "local overlay '$LOCAL_ENV' not found" >&2; exit 1; }

# Read one key out of an env file without polluting this shell.
# Prove a layer parses before any lookup reads from it. val_from() below sources
# in a subshell and cannot abort the script from every call site (several are in
# `||` lists, where set -e does not apply), so a parse error there was
# indistinguishable from a missing key: an unterminated quote anywhere in the
# file aborted the source, every subsequent lookup returned empty, and a MinIO
# secret containing ' was emitted as MINIO_SECRET_KEY='' — an opaque 403 later.
require_parsable() {
  [ -f "$1" ] || return 0
  ( set -a; . "$1" >/dev/null 2>&1 ) && return 0
  echo "error: $1 is not valid shell — it is read by '.'-sourcing, so every" \
       "value must be quoted (NAME='value'). A single unbalanced quote makes" \
       "every value in the file read as empty." >&2
  exit 2
}
# Only the layers every profile reads. env/remote.env is validated after the
# toggles resolve (see below) — a fully-offline `make env PROFILE=local` never
# reads it, so a stale or malformed copy must not block generating a profile
# that does not use the cluster at all.
require_parsable "$BASE_ENV"
require_parsable "$LOCAL_ENV"

val_from() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 0
  ( set -a; . "$file" >/dev/null 2>&1 || true; set +a; printf '%s' "${!key-}" )
}

# ─── Resolve the toggles ─────────────────────────────────────────────────────
PI_PROFILE="${PI_PROFILE:-remote}"
case "$PI_PROFILE" in
  remote) d_store=remote; d_docling=remote ;;
  local)  d_store=local;  d_docling=local  ;;
  hybrid) d_store=local;  d_docling=remote ;;
  *) echo "PI_PROFILE must be remote|local|hybrid (got '$PI_PROFILE')" >&2; exit 2 ;;
esac

PI_MINIO="${PI_MINIO:-$d_store}"
PI_REDIS="${PI_REDIS:-$d_store}"
PI_POSTGRES="${PI_POSTGRES:-$d_store}"
# Docling defaults to remote under every profile except an explicit `local`
# one: the Scaleway service is always reachable, so there is rarely a reason
# to pay for a 1.9 GB local container.
PI_DOCLING="${PI_DOCLING:-$d_docling}"
PI_APP="${PI_APP:-host}"
PI_MINIO_ACCESS="${PI_MINIO_ACCESS:-auto}"

# Both checks below are deferred until the toggles resolve, not run at startup:
# a fully-offline `make env PROFILE=local` never reads the remote side, so
# neither a missing nor a malformed env/remote.env should block it — one would
# be noise about a cluster the user is deliberately not using, the other would
# refuse to generate a profile that does not touch it.
USES_REMOTE=0
if [ "$PI_MINIO" = remote ] || [ "$PI_REDIS" = remote ] \
   || [ "$PI_POSTGRES" = remote ] || [ "$PI_DOCLING" = remote ]; then
  USES_REMOTE=1
  require_parsable "$REMOTE_ENV"
fi
if [ ! -f "$REMOTE_ENV" ] && [ "$USES_REMOTE" = 1 ]; then
  warn "$REMOTE_ENV is missing but this profile uses remote services — run 'make env-remote' on the k3s node, then copy the generated file back to here."
fi

for pair in "PI_MINIO:$PI_MINIO" "PI_REDIS:$PI_REDIS" "PI_POSTGRES:$PI_POSTGRES" "PI_DOCLING:$PI_DOCLING"; do
  case "${pair#*:}" in remote|local) ;; *) echo "${pair%%:*} must be remote|local (got '${pair#*:}')" >&2; exit 2 ;; esac
done
case "$PI_APP" in host|compose) ;; *) echo "PI_APP must be host|compose (got '$PI_APP')" >&2; exit 2 ;; esac
case "$PI_MINIO_ACCESS" in auto|cluster|public) ;; *) echo "PI_MINIO_ACCESS must be auto|cluster|public" >&2; exit 2 ;; esac

# ─── MinIO ───────────────────────────────────────────────────────────────────
tcp_open() {  # host port [timeout]
  local h="$1" p="$2" t="${3:-2}"
  timeout "$t" bash -c "exec 3<>/dev/tcp/$h/$p" 2>/dev/null
}

MINIO_ACCESS_MODE="n/a"
if [ "$PI_MINIO" = "local" ]; then
  M_ENDPOINT="$(val_from "$LOCAL_ENV" PI_LOCAL_MINIO_ENDPOINT)"
  M_KEY="$(val_from "$LOCAL_ENV" PI_LOCAL_MINIO_ACCESS_KEY)"
  M_SECRET="$(val_from "$LOCAL_ENV" PI_LOCAL_MINIO_SECRET_KEY)"
  M_SECURE="$(val_from "$LOCAL_ENV" PI_LOCAL_MINIO_SECURE)"
  M_PRESIGN="$(val_from "$LOCAL_ENV" PI_LOCAL_MINIO_PRESIGN_ENDPOINT)"
  M_PRESIGN_SECURE="$(val_from "$LOCAL_ENV" PI_LOCAL_MINIO_PRESIGN_SECURE)"
  # Fill in the presign host the local Docling container can actually reach.
  if [ -z "$M_PRESIGN" ] && [ "$PI_DOCLING" = "local" ]; then
    if [ "$PI_APP" = "compose" ]; then
      M_PRESIGN="minio:9000"
    else
      # `|| true`: iproute2 is Linux-only and absent from macOS and slim
      # images. Without it, set -o pipefail turns a missing `ip` into rc=127
      # and set -e aborts the whole run — with 2>/dev/null hiding why. The
      # docker0 default below is the answer that lookup would have produced
      # anyway on a stock Docker host.
      M_PRESIGN="$( { ip route 2>/dev/null || true; } | awk '/docker0/ {print $NF; exit}')"
      M_PRESIGN="${M_PRESIGN:-172.17.0.1}:9000"
    fi
  fi
  # In-compose services address each other by service name, not localhost.
  if [ "$PI_APP" = "compose" ]; then
    M_ENDPOINT="minio:9000"
  fi
else
  M_CLUSTER="$(val_from "$REMOTE_ENV" PI_REMOTE_MINIO_CLUSTER_ENDPOINT)"
  M_PUBLIC="$(val_from "$REMOTE_ENV" PI_REMOTE_MINIO_PUBLIC_ENDPOINT)"
  M_KEY="$(val_from "$REMOTE_ENV" PI_REMOTE_MINIO_ACCESS_KEY)"
  M_SECRET="$(val_from "$REMOTE_ENV" PI_REMOTE_MINIO_SECRET_KEY)"

  M_ROUTE_PREFIX="$(val_from "$REMOTE_ENV" PI_REMOTE_MINIO_PRESIGN_PATH_PREFIX)"

  # The public host serves MinIO under a stripped route prefix. Direct S3 calls
  # carry it too — applied in the HTTP client after signing, which is exactly
  # what the proxy strips back off (see src/pageindex_mcp/minio_client.py). The
  # ClusterIP addresses MinIO directly, so it takes no prefix.
  MINIO_ACCESS_MODE="$PI_MINIO_ACCESS"
  if [ "$MINIO_ACCESS_MODE" = "auto" ]; then
    if [ -n "$M_CLUSTER" ] && tcp_open "${M_CLUSTER%%:*}" "${M_CLUSTER##*:}"; then
      MINIO_ACCESS_MODE="cluster (auto: ClusterIP reachable)"
      M_ENDPOINT="$M_CLUSTER"; M_SECURE=false; M_PREFIX=""
    else
      MINIO_ACCESS_MODE="public (auto: ClusterIP unreachable)"
      # Same guard as the explicit `public` branch below. Without it an
      # unreachable ClusterIP plus an unconfigured public host silently emits
      # MINIO_ENDPOINT= and the failure surfaces much later, far from the cause.
      [ -n "$M_PUBLIC" ] || { echo "ClusterIP $M_CLUSTER is unreachable and PI_REMOTE_MINIO_PUBLIC_ENDPOINT is unset in $REMOTE_ENV — no usable MinIO address" >&2; exit 2; }
      M_ENDPOINT="$M_PUBLIC"; M_SECURE=true; M_PREFIX="$M_ROUTE_PREFIX"
    fi
  elif [ "$MINIO_ACCESS_MODE" = "cluster" ]; then
    M_ENDPOINT="$M_CLUSTER"; M_SECURE=false; M_PREFIX=""
  else
    M_ENDPOINT="$M_PUBLIC"; M_SECURE=true; M_PREFIX="$M_ROUTE_PREFIX"
    [ -n "$M_PUBLIC" ] || { echo "PI_MINIO_ACCESS=public but PI_REMOTE_MINIO_PUBLIC_ENDPOINT is unset in $REMOTE_ENV" >&2; exit 2; }
  fi

  # The presign host is what the *Docling service* fetches from, which is a
  # different question from what this machine talks to. It must always be the
  # public one — a ClusterIP means nothing to Scaleway.
  M_PRESIGN="$M_PUBLIC"
  M_PRESIGN_SECURE=true
  M_PRESIGN_PREFIX="$M_ROUTE_PREFIX"
fi

# ─── Redis / Postgres ────────────────────────────────────────────────────────
if [ "$PI_REDIS" = "local" ]; then
  R_URL="$(val_from "$LOCAL_ENV" PI_LOCAL_REDIS_URL)"
  [ "$PI_APP" = "compose" ] && R_URL="redis://redis:6379/1"
else
  R_URL="$(val_from "$REMOTE_ENV" PI_REMOTE_REDIS_URL)"
fi

if [ "$PI_POSTGRES" = "local" ]; then
  P_DSN="$(val_from "$LOCAL_ENV" PI_LOCAL_POSTGRES_DSN)"
  [ "$PI_APP" = "compose" ] && P_DSN="postgresql://pageindex:pageindex@postgres:5432/pageindex"
else
  P_DSN="$(val_from "$REMOTE_ENV" PI_REMOTE_POSTGRES_DSN)"
fi

# ─── Docling ─────────────────────────────────────────────────────────────────
if [ "$PI_DOCLING" = "local" ]; then
  D_URL="$(val_from "$LOCAL_ENV" PI_LOCAL_DOCLING_URL)"
  D_TOKEN="$(val_from "$LOCAL_ENV" PI_LOCAL_DOCLING_TOKEN)"
  [ "$PI_APP" = "compose" ] && D_URL="http://docling-service:8080"
else
  # The Scaleway URL + token live in .env — one copy, no duplication into a
  # second file that would then need its own secret handling.
  D_URL="$(val_from "$REMOTE_ENV" PI_REMOTE_DOCLING_URL)"
  D_TOKEN="$(val_from "$REMOTE_ENV" PI_REMOTE_DOCLING_TOKEN)"
  [ -n "$D_URL" ]   || D_URL="$(val_from "$BASE_ENV" DOCLING_SERVICE_URL)"
  [ -n "$D_TOKEN" ] || D_TOKEN="$(val_from "$BASE_ENV" DOCLING_SERVICE_BEARER_TOKEN)"
fi

# ─── Cross-toggle sanity ─────────────────────────────────────────────────────
# The remote Docling service is handed a presigned URL and fetches the object
# itself. Every combination below is about whether it can reach that URL.
if [ "$PI_DOCLING" = "remote" ] && [ "$PI_MINIO" = "local" ]; then
  warn "PI_DOCLING=remote + PI_MINIO=local: the Scaleway service cannot fetch a presigned URL pointing at your localhost MinIO. PDFs and images will fail; every other format is fine. Use PI_PROFILE=local, or PI_MINIO=remote."
fi
if [ "$PI_DOCLING" = "remote" ] && [ "$PI_MINIO" = "remote" ] && [ -z "$M_PRESIGN" ]; then
  warn "No public MinIO host is configured, so presigned URLs are unreachable from Scaleway. PDF/image ingestion will fail; other formats are unaffected. See docs/ENV_PROFILES.md."
fi
if [ "$PI_DOCLING" = "local" ] && [ "$PI_MINIO" = "remote" ] && [ "$MINIO_ACCESS_MODE" != "n/a" ]; then
  case "$MINIO_ACCESS_MODE" in
    cluster*) warn "PI_DOCLING=local + remote MinIO over the ClusterIP: the docling container must be able to reach $M_ENDPOINT. It usually cannot from a laptop." ;;
  esac
fi

# ─── Emit ────────────────────────────────────────────────────────────────────
MANAGED='^(MINIO_ENDPOINT|MINIO_ACCESS_KEY|MINIO_SECRET_KEY|MINIO_SECURE|MINIO_PATH_PREFIX|MINIO_PRESIGN_ENDPOINT|MINIO_PRESIGN_SECURE|MINIO_PRESIGN_PATH_PREFIX|REDIS_URL|POSTGRES_DSN|DOCLING_SERVICE_URL|DOCLING_SERVICE_BEARER_TOKEN|PI_DOCLING)='

summary() {
  printf '  profile   %s   (app: %s)\n' "$PI_PROFILE" "$PI_APP"
  printf '  minio     %-7s %s%s%s\n' "$PI_MINIO" "$M_ENDPOINT" "${M_PREFIX:-}" \
    "$([ "$MINIO_ACCESS_MODE" = "n/a" ] && echo "" || echo "   [$MINIO_ACCESS_MODE]")"
  presign_display="<unset — remote Docling cannot fetch>"
  [ -n "$M_PRESIGN" ] && presign_display="${M_PRESIGN}${M_PRESIGN_PREFIX:-}"
  printf '  presign   %-7s %s\n' "" "$presign_display"
  printf '  redis     %-7s %s\n' "$PI_REDIS" "$(printf '%s' "$R_URL" | sed -E 's#//[^@]*@#//***@#')"
  printf '  postgres  %-7s %s\n' "$PI_POSTGRES" "$(printf '%s' "$P_DSN" | sed -E 's#//[^@]*@#//***@#')"
  printf '  docling   %-7s %s\n' "$PI_DOCLING" "${D_URL:-<unset>}"
}

if [ "$SHOW_ONLY" = 1 ]; then
  echo "resolved profile (nothing written):"
  summary
else
  # Build in a temp file and rename only on success. emit() can abort partway
  # through (an unserializable credential), and a truncated .env.active is worse
  # than none: it is what every other target sources, so it would fail later
  # with a missing-variable error rather than the real reason.
  TMP="$(mktemp "${OUT}.XXXXXX")"
  chmod 600 "$TMP"
  trap 'rm -f "$TMP"' EXIT
  # `|| [ $? -eq 1 ]`: grep exits 1 when nothing matches, which under set -e
  # would abort on the legitimate case of a base file that is empty or holds
  # only managed keys. Status 2+ is a real grep error and still aborts.
  grep -vE "$MANAGED" "$BASE_ENV" > "$TMP" || [ $? -eq 1 ]
  cat >> "$TMP" <<EOF

# ─── Resolved by scripts/env_profile.sh — DO NOT EDIT ────────────────────────
# Regenerate with 'make env'. Values below are single-quoted by emit() so the
# shell and dotenv parsers agree; do not hand-edit them unquoted. Toggles:
#   PI_PROFILE=$PI_PROFILE PI_APP=$PI_APP PI_MINIO=$PI_MINIO PI_REDIS=$PI_REDIS
#   PI_POSTGRES=$PI_POSTGRES PI_DOCLING=$PI_DOCLING PI_MINIO_ACCESS=$MINIO_ACCESS_MODE
EOF
  {
    emit MINIO_ENDPOINT             "$M_ENDPOINT"
    emit MINIO_ACCESS_KEY           "$M_KEY"
    emit MINIO_SECRET_KEY           "$M_SECRET"
    emit MINIO_SECURE               "$M_SECURE"
    emit MINIO_PATH_PREFIX          "${M_PREFIX:-}"
    emit MINIO_PRESIGN_ENDPOINT     "$M_PRESIGN"
    emit MINIO_PRESIGN_SECURE       "${M_PRESIGN_SECURE:-true}"
    emit MINIO_PRESIGN_PATH_PREFIX  "${M_PRESIGN_PREFIX:-}"
    emit REDIS_URL                  "$R_URL"
    emit POSTGRES_DSN               "$P_DSN"
    emit DOCLING_SERVICE_URL        "$D_URL"
    emit DOCLING_SERVICE_BEARER_TOKEN "$D_TOKEN"
    # Emitted as a real variable, not just the comment above, so make targets
    # can guard on the resolved toggle (see the compose-docling recipe).
    emit PI_DOCLING                 "$PI_DOCLING"
  } >> "$TMP"
  mv "$TMP" "$OUT"
  trap - EXIT
  echo "wrote $OUT (mode 600)"
  summary
fi

if [ ${#WARNINGS[@]} -gt 0 ]; then
  echo
  for w in "${WARNINGS[@]}"; do
    echo "WARNING: $w" | fold -s -w 78 | sed '2,$s/^/         /'
  done
fi
