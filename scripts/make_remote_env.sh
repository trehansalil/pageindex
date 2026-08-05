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

# Serialize NAME=value for a file that is both `.`-sourced by make targets and
# parsed as dotenv by `docker compose --env-file` / python-dotenv. Single quotes
# are the only form all three treat identically, because none of them process
# escapes inside them; double quotes diverge (a shell unescapes \$ and \`,
# dotenv leaves them literal). Unquoted is unsafe outright — a secret containing
# a space, $, or ` would truncate the value or execute as shell code.
#
# A literal single quote is the one character no single form can express: the
# `'\''` splice below is correct for the shell but dotenv parsers cannot read
# it, so that case warns rather than silently emitting something one consumer
# will misread.
emit() {
  local v="${2-}" q="'" esc="'\\''"
  case "$v" in
    *"$q"*) echo "warning: $1 contains a single quote — correct for 'make'/shell," \
                 "but 'docker compose --env-file' cannot parse it." >&2 ;;
  esac
  printf "%s='%s'\n" "$1" "${v//$q/$esc}"
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
# It changes on pod restart — rerun this script after any churn. Select on
# Ready and wait: mid-restart the first matching pod is Pending with no IP,
# which used to be written into the DSN verbatim.
kubectl wait --for=condition=Ready pod -n "$NS" -l app=postgres --timeout=60s >/dev/null 2>&1 || true
PG_IP="$(require_ip "pod app=postgres" "$(kubectl get pod -n "$NS" -l app=postgres \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].status.podIP}' 2>/dev/null)")"

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
} > "$OUT"

chmod 600 "$OUT"
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
