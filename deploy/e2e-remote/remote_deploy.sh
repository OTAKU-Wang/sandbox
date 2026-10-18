#!/usr/bin/env bash
# CDS remote e2e deploy (SS-14): scripted docker run because the openEuler host has no compose v2 plugin.
# Mirrors docker-compose.yml (root variant) services 1:1; idempotent (volumes/network persist).
set -euo pipefail

ENV_FILE="${1:-/root/cds.env}"
[ -f "$ENV_FILE" ] || { echo "FATAL: missing env file: $ENV_FILE"; exit 1; }
set -a; source "$ENV_FILE"; set +a

for v in POSTGRES_PASSWORD REDIS_PASSWORD CLICKHOUSE_PASSWORD MINIO_ROOT_PASSWORD VAULT_TOKEN \
         CDS_JWT_SECRET_KEY CDS_KMS_SERVICE_TOKEN CDS_CUBE_AUDIT_SERVICE_TOKEN CDS_CUBE_TEMPLATE_SIGNING_KEY; do
  if [ -z "${!v:-}" ]; then echo "FATAL: $v is empty in $ENV_FILE"; exit 1; fi
done
CUBE_DP="${CDS_CUBE_DATAPLANE_ENABLED:-false}"
CUBE_TLS="${CDS_CUBE_NODE_GRPC_TLS:-false}"
CUBE_EGRESS_CA="${CDS_CUBE_EGRESS_CA:-}"

NET=cds-net
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET"

vol() { docker volume create "cds_$1" >/dev/null; echo "cds_$1"; }

run() {
  local name="$1" image="$2"; shift 2
  # docker run treats everything after the image name as the container command,
  # so every flag (-e/-p/-v) must precede the image name; call sites that need a
  # custom command separate it with a literal `--` (split here, passed after the image).
  local args=() cmd=() seen=0 a
  for a in "$@"; do
    if [ "$seen" = "1" ]; then cmd+=("$a"); else if [ "$a" = "--" ]; then seen=1; else args+=("$a"); fi; fi
  done
  docker rm -f "$name" >/dev/null 2>&1 || true
  # a crash-looping old container's restart policy races the re-create and can
  # briefly hold the published port inside its netns — wait for full teardown.
  local _i
  for _i in 1 2 3 4 5 6 7 8 9 10; do
    docker ps -q -f name="^${name}$" | grep -q . || break
    sleep 1
  done
  docker run -d --name "$name" --network "$NET" --restart unless-stopped "${args[@]}" "$image" "${cmd[@]}"
}

wait_healthy() {
  local name="$1" cmd="$2" tries="${3:-30}" i=0
  until docker exec "$name" $cmd >/dev/null 2>&1; do
    i=$((i+1))
    if [ "$i" -ge "$tries" ]; then echo "FATAL: $name not healthy after $i tries: $cmd"; docker logs --tail 20 "$name" || true; exit 1; fi
    sleep 2
  done
  echo "healthy: $name"
}

# openpolicyagent/opa image ships no wget/curl, so its health is probed from the
# host through the published port instead of docker exec.
wait_healthy_port() {
  local port="$1" tries="${2:-30}" i=0
  until curl -fsS "http://localhost:${port}/health" >/dev/null 2>&1; do
    i=$((i+1))
    if [ "$i" -ge "$tries" ]; then echo "FATAL: port $port not healthy after $i tries"; exit 1; fi
    sleep 2
  done
  echo "healthy: port $port"
}

echo "== middleware =="
run cds-postgres postgres:16-alpine \
  -e POSTGRES_DB=cds -e POSTGRES_USER=cds -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -p 5432:5432 -v "$(vol postgres-data):/var/lib/postgresql/data"

run cds-redis redis:7-alpine \
  -p 6379:6379 -v "$(vol redis-data):/data" \
  -- redis-server --requirepass "$REDIS_PASSWORD" --maxmemory 256mb --maxmemory-policy allkeys-lru

run cds-clickhouse clickhouse/clickhouse-server:24.3-alpine \
  -e CLICKHOUSE_DB=cds_audit -e CLICKHOUSE_USER=cds -e CLICKHOUSE_PASSWORD="$CLICKHOUSE_PASSWORD" \
  -p 8123:8123 -p 9000:9000 -v "$(vol clickhouse-data):/var/lib/clickhouse"

run cds-minio minio/minio:RELEASE.2025-01-20T14-49-07Z \
  -e MINIO_ROOT_USER="${MINIO_ROOT_USER:-cds_minio_admin}" -e MINIO_ROOT_PASSWORD="$MINIO_ROOT_PASSWORD" \
  -p 9001:9000 -p 9002:9001 -v "$(vol minio-data):/data" \
  -- server /data --console-address ":9001"

run cds-opa openpolicyagent/opa:0.68.0 \
  -p 8181:8181 \
  -- run --server --addr=0.0.0.0:8181 --log-level=info

# pure dev mode (in-mem, no volume): idempotent across re-deploys — a file
# backend would leave "already initialized" state and refuse the re-run.
# dev listener owns the 8200 bind (a second listener in VAULT_LOCAL_CONFIG on
# the same address = "Error parsing listener configuration" self-collision).
run cds-vault hashicorp/vault:1.15 \
  --cap-add IPC_LOCK \
  -e VAULT_DEV_ROOT_TOKEN_ID="$VAULT_TOKEN" -e VAULT_DEV_LISTEN_ADDRESS=0.0.0.0:8200 \
  -p 8200:8200

echo "== waiting for middleware =="
wait_healthy cds-postgres "pg_isready -U cds" 45
wait_healthy cds-redis "redis-cli -a $REDIS_PASSWORD ping" 45
wait_healthy cds-minio "mc ready local" 45
wait_healthy_port 8181 30
# a vault crash-loop from an earlier failed deploy can leave its restart cycle
# holding port 8200 briefly; allow the restart backoff to settle before FATAL.
wait_healthy cds-vault "vault status -address=http://localhost:8200" 60
# D-e2e-11: the app KMS uses Vault TRANSIT for KEK management (hsm_adapter
# VaultTransitAdapter). The dev-mode Vault ships without the transit engine,
# so every KEK op 500s with "HSM unavailable — cannot generate KEK". Enable
# it on every deploy (dev Vault is in-memory and resets on container restart).
VROOT="$(docker exec cds-vault printenv VAULT_DEV_ROOT_TOKEN_ID)"
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN="$VROOT" \
  cds-vault vault secrets enable -path=transit transit 2>/dev/null \
  || docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN="$VROOT" \
  cds-vault vault secrets list 2>/dev/null | grep -q transit \
  || { echo "FATAL: could not enable Vault transit engine"; exit 1; }

echo "== api =="
# e2e environment: DEBUG=true relaxes the prod startup safety gates (this is a
# functional test stack, not a release); sandbox/auth/egress behavior is fully
# active regardless. Federation + column-encryption key are env-driven so the
# e2e env can pin compliant values (federation stack is not deployed remote).
run cds-api cds-api:latest \
  -p 8000:8000 \
  -e CDS_DEBUG="${CDS_DEBUG:-true}" \
  -e CDS_FEDERATION_ENABLED="${CDS_FEDERATION_ENABLED:-false}" \
  -e CDS_COLUMN_ENCRYPTION_KEY="${CDS_COLUMN_ENCRYPTION_KEY:-}" \
  -e CDS_SM3_FALLBACK_ALLOWED="${CDS_SM3_FALLBACK_ALLOWED:-true}" \
  -e CDS_FEDERATION_CA_BUNDLE="${CDS_FEDERATION_CA_BUNDLE:-}" \
  -e CDS_DATABASE_URL="postgresql+asyncpg://cds:${POSTGRES_PASSWORD}@cds-postgres:5432/cds" \
  -e CDS_REDIS_URL="redis://:${REDIS_PASSWORD}@cds-redis:6379/0" \
  -e CDS_CLICKHOUSE_URL="http://cds-clickhouse:8123/cds_audit" \
  -e CDS_OPA_URL="http://cds-opa:8181" \
  -e CDS_VAULT_ADDR="http://cds-vault:8200" \
  -e CDS_VAULT_TOKEN="$VAULT_TOKEN" \
  -e CDS_MINIO_ENDPOINT="cds-minio:9000" \
  -e CDS_MINIO_ACCESS_KEY="${MINIO_ROOT_USER:-cds_minio_admin}" \
  -e CDS_MINIO_SECRET_KEY="$MINIO_ROOT_PASSWORD" \
  -e CDS_JWT_SECRET_KEY="$CDS_JWT_SECRET_KEY" \
  -e CDS_MIA_SHADOW_MODEL="false" \
  -e CDS_FEDERATION_CERT="${CDS_FEDERATION_CERT:-}" \
  -e CDS_FEDERATION_KEY="" \
  -e CDS_ATTESTATION_ALLOWED_MEASUREMENTS="" \
  -e CDS_SESSION_CLEANUP_INTERVAL_SECONDS="300" \
  -e CDS_KMS_REQUIRE_ATTESTATION="${CDS_KMS_REQUIRE_ATTESTATION:-false}" \
  -e CDS_KMS_SERVICE_TOKEN="$CDS_KMS_SERVICE_TOKEN" \
  -e CDS_DEV_SANDBOX_REQUIRE_CONTRACT="false" \
  -e CDS_SESSION_DEFAULT_IDLE_POLICY="kill" \
  -e CDS_EXEC_STREAM_MAX_CONCURRENT="3" \
  -e CDS_AUTH_MFA_ENABLED="${CDS_AUTH_MFA_ENABLED:-true}" \
  -e CDS_MFA_SECRET_ENCRYPTION_KEY="${CDS_MFA_SECRET_ENCRYPTION_KEY:-}" \
  -e CDS_AUTH_CERT_LOGIN="true" \
  -e CDS_AGENT_STEP_BUDGET="15" \
  -e CDS_AGENT_TOKEN_BUDGET="20000" \
  -e CDS_AGENT_MAX_TOOLS="4" \
  -e CDS_CUBE_DATAPLANE_ENABLED="$CUBE_DP" \
  -e CDS_LEGACY_L2_ENABLED="false" \
  -e CDS_SANDBOX_LEGACY_ENABLED="${CDS_SANDBOX_LEGACY_ENABLED:-false}" \
  -e CDS_CUBE_NODE_GRPC_TLS="$CUBE_TLS" \
  -e CDS_CUBE_NODE_TLS_PROXY_PORT="9443" \
  -e CDS_CUBE_RESULTS_DIR=/var/lib/cds/sandbox-results \
  -e CDS_CUBE_SANDBOX_IMAGE="cds/cube-sandbox:latest" \
  -e CDS_CUBE_DEK_LENGTH="16" \
  -e CDS_CUBE_EGRESS_CA="$CUBE_EGRESS_CA" \
  -e CDS_CUBE_AUDIT_SERVICE_TOKEN="$CDS_CUBE_AUDIT_SERVICE_TOKEN" \
  -e CDS_CUBE_TEMPLATE_SIGNING_KEY="$CDS_CUBE_TEMPLATE_SIGNING_KEY" \
  -e CDS_CUBE_TEMPLATE_URL_TTL_SECONDS="3600" \
  -e CDS_CUBE_TEMPLATE_DIST_RETRY="2" \
  -e CDS_RETENTION_JANITOR_ENABLED="true" \
  -e CDS_RETENTION_JANITOR_DRY_RUN="true" \
  -e CDS_MERKLE_PIPELINE_ENABLED="false" \
  -e CDS_EGRESS_AUDIT_ENABLED="true" \
  -e CDS_NODE_STALE_SECONDS="300" \
  -e CDS_SHARED_VOLUME_ROOT=/data/shared-volumes \
  -e CDS_EGRESS_AUDIT_ENCRYPTION_ENABLED="true" \
  -e CDS_RATE_LIMIT_ENABLED="true" \
  -e CDS_RATE_LIMIT_AUTH_PER_MINUTE="10" \
  -v "$(vol sandbox-workspace):/tmp/cds-sandbox" \
  -v "$(vol sandbox-workspace-l0):/tmp/cds-sandbox-l0" \
  -v "$(vol cds-storage):/tmp/cds-storage"

echo "== waiting for api /health =="
i=0
until curl -fsS http://localhost:8000/health >/dev/null 2>&1; do
  i=$((i+1))
  if [ "$i" -ge 60 ]; then echo "FATAL: cds-api /health not up after 60 tries"; docker logs --tail 30 cds-api || true; exit 1; fi
  sleep 2
done
echo "healthy: cds-api"

echo "== frontend =="
run cds-frontend cds-frontend:latest -p 3000:80

echo "== summary =="
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
curl -fsS http://localhost:8000/health; echo
echo DEPLOY_DONE
