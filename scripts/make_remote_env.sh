#!/usr/bin/env bash
# make_remote_env.sh — snapshot the shared k3s `infra` namespace into
# env/remote.env, the "remote" side of every toggle in scripts/env_profile.sh.
#
#   ./scripts/make_remote_env.sh              # writes env/remote.env
#   ./scripts/make_remote_env.sh /tmp/foo.env # writes elsewhere
#
# Run this ON the k3s node (it needs kubectl). To test from a laptop, copy the
# generated file over — it is self-contained and holds no ClusterIP dependency
# beyond the cluster-side endpoints, which env_profile.sh will skip when they
# are unreachable.
#
# Secrets are read from the cluster at generation time; none are stored in this
# script. The output is gitignored — treat it as a credential file.
set -euo pipefail

cd "$(dirname "$0")/.."

NS="${INFRA_NAMESPACE:-infra}"
OUT="${1:-env/remote.env}"
BASE="${BASE_ENV:-.env}"

# Public HTTPS host for MinIO — the host a presigned URL names so the remote
# Docling service can fetch the object.
# ${VAR-default}, not ${VAR:-default}: an explicitly empty value must survive
# so the "no public endpoint" warning at the bottom can actually fire.
PUBLIC="${PI_REMOTE_MINIO_PUBLIC_ENDPOINT-infra.saliltrehan.com}"

# Route prefix the public MinIO S3 API is served under. Traefik strips it
# before MinIO sees the request, which is exactly why this works: MinIO
# verifies the SigV4 signature against the stripped path — the same path the
# SDK signs — so the prefix is spliced in *after* signing. Verified live:
# https://<PUBLIC>/minio/<bucket>/<key>?<sig> returns 200, while the unprefixed
# signed path 404s at Traefik and an unsigned request gets 403 from MinIO.
PRESIGN_PREFIX="${PI_REMOTE_MINIO_PRESIGN_PATH_PREFIX:-/minio}"

command -v kubectl >/dev/null || { echo "missing required tool: kubectl" >&2; exit 1; }

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
# So a value holding ' or \ cannot be written such that every consumer reads the
# same bytes back, and the Python side of that divergence is silent. Reject here
# rather than ship a profile whose secrets differ by who reads them. (The DSN is
# unaffected either way — uri_enc percent-encodes both characters first.)
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

# Percent-encode a URI userinfo component (RFC 3986 unreserved set). A password
# containing @ : / ? # otherwise splits the DSN at the wrong place and the
# driver connects to the wrong host — or fails to parse at all.
# LC_ALL=C makes the loop byte-wise, which is what RFC 3986 wants for UTF-8.
uri_enc() {
  local LC_ALL=C s="${1-}" out="" i c
  for (( i = 0; i < ${#s}; i++ )); do
    c="${s:i:1}"
    case "$c" in
      [A-Za-z0-9._~-]) out+="$c" ;;
      *) out+="$(printf '%%%02X' "'$c")" ;;
    esac
  done
  printf '%s' "$out"
}

# A ClusterIP/pod IP that came back empty means the service or pod is not
# Ready. Writing it anyway produces a profile that fails much later with a
# confusing connection error, so fail here where the cause is obvious.
require_ip() {
  [ -n "${2-}" ] || { echo "$1 has no IP yet — is the pod Ready? (kubectl get pod -n $NS)" >&2; exit 1; }
  printf '%s' "$2"
}

secret() { kubectl get secret infra-secrets -n "$NS" -o "jsonpath={.data.$1}" | base64 -d; }
svc_ip() { kubectl get svc "$1" -n "$NS" -o jsonpath='{.spec.clusterIP}'; }

MINIO_IP="$(require_ip "svc/minio" "$(svc_ip minio)")"
REDIS_IP="$(require_ip "svc/redis" "$(svc_ip redis)")"
# postgres is a headless StatefulSet service, so resolve the pod IP directly.
# It changes on pod restart — rerun this script after any churn.
#
# The wait must be allowed to fail. Running != Ready: a postgres mid-recovery
# has an IP and reports Running while still refusing connections, so swallowing
# a timeout here bakes an unusable host into the DSN and defers the error to
# whatever runs next, with no hint that the cause was a pod that never came up.
kubectl wait --for=condition=Ready pod -n "$NS" -l app=postgres --timeout=60s >/dev/null
# Select on the Ready condition too, not just phase: with more than one pod
# matching the label, `wait` succeeding says nothing about which one [0] is.
PG_IP="$(require_ip "pod app=postgres" "$(kubectl get pod -n "$NS" -l app=postgres \
  -o jsonpath='{.items[?(@.status.conditions[?(@.type=="Ready")].status=="True")].status.podIP}' \
  2>/dev/null | awk '{print $1}')")"

MINIO_USER="$(secret MINIO_ROOT_USER)"
MINIO_PASS="$(secret MINIO_ROOT_PASSWORD)"
PG_USER="$(secret POSTGRES_USER)"
PG_PASS="$(secret POSTGRES_PASSWORD)"

# The Scaleway Docling endpoint is not a cluster resource; carry it over from
# .env so the credential lives in exactly one place.
doc_val() { ( set -a; . "$BASE" >/dev/null 2>&1 || true; set +a; printf '%s' "${!1-}" ); }
DOCLING_URL="${PI_REMOTE_DOCLING_URL:-$(doc_val DOCLING_SERVICE_URL)}"
DOCLING_TOKEN="${PI_REMOTE_DOCLING_TOKEN:-$(doc_val DOCLING_SERVICE_BEARER_TOKEN)}"

mkdir -p "$(dirname "$OUT")"
# Build in a temp file and rename only on success: emit() aborts partway through
# on an unserializable credential, and a half-written env/remote.env would be
# read by `make env` as if it were complete.
TMP="$(mktemp "${OUT}.XXXXXX")"
chmod 600 "$TMP"
trap 'rm -f "$TMP"' EXIT
{
cat <<EOF
# env/remote.env — generated by scripts/make_remote_env.sh on $(date -u +%FT%TZ)
# from the k3s '$NS' namespace. Credential file: gitignored, mode 600.
# Regenerate after any cluster IP change (especially a postgres pod restart).
# Values are emitted quoted/escaped by emit() — do not hand-edit unquoted.

# In-cluster address: fast, but only routable on the k3s node itself.
EOF
emit PI_REMOTE_MINIO_CLUSTER_ENDPOINT "${MINIO_IP}:9000"
cat <<'EOF'
# Public HTTPS address the remote Docling service fetches presigned URLs from.
# Presign-only: the SDK refuses a path in an endpoint ("path in endpoint is not
# allowed"), so direct S3 calls cannot use the prefixed route — see
# docs/ENV_PROFILES.md.
EOF
emit PI_REMOTE_MINIO_PUBLIC_ENDPOINT "$PUBLIC"
emit PI_REMOTE_MINIO_PRESIGN_PATH_PREFIX "$PRESIGN_PREFIX"
emit PI_REMOTE_MINIO_ACCESS_KEY "$MINIO_USER"
emit PI_REMOTE_MINIO_SECRET_KEY "$MINIO_PASS"
echo
emit PI_REMOTE_REDIS_URL "redis://${REDIS_IP}:6379/1"
emit PI_REMOTE_POSTGRES_DSN \
  "postgresql://$(uri_enc "$PG_USER"):$(uri_enc "$PG_PASS")@${PG_IP}:5432/pageindex"
echo
emit PI_REMOTE_DOCLING_URL "$DOCLING_URL"
emit PI_REMOTE_DOCLING_TOKEN "$DOCLING_TOKEN"
} > "$TMP"

mv "$TMP" "$OUT"
trap - EXIT
echo "wrote $OUT (mode 600)"
echo "  MinIO    ${MINIO_IP}:9000   presign: ${PUBLIC:-<none>}${PRESIGN_PREFIX}"
echo "  Redis    ${REDIS_IP}:6379"
echo "  Postgres ${PG_IP}:5432"
echo "  Docling  ${DOCLING_URL:-<none>}"
echo
echo "Next: make env      # resolve toggles into .env.active"

[ -n "$PUBLIC" ] || cat <<'WARN'

NOTE: PI_REMOTE_MINIO_PUBLIC_ENDPOINT is empty, so the remote Docling service
cannot fetch presigned URLs — PDF/image ingestion will fail, other formats are
unaffected. Set it to the public MinIO host:

  PI_REMOTE_MINIO_PUBLIC_ENDPOINT=minio.example.com ./scripts/make_remote_env.sh

See docs/ENV_PROFILES.md.
WARN
