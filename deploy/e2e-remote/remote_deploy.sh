#!/usr/bin/env bash
# CDS remote e2e deploy (SS-14): scripted docker run because the openEuler host has no compose v2 plugin.
# Mirrors docker-compose.yml (root variant) services 1:1; idempotent (volumes/network persist).
set -euo pipefail

ENV_FILE="${1:-/root/cds.env}"
[ -f "$ENV_FILE" ] || { echo "FATAL: missing env file: $ENV_FILE"; exit 1; }

# No compose on this host → this script is the compose. Serialization guard:
# the lane is re-triggered over SSH/CI; two concurrent runs would rm -f each
# other's containers and fight over the fixed published ports.
if command -v flock >/dev/null 2>&1; then
  exec 9>/tmp/cds-remote-deploy.lock
  flock -n 9 || { echo "FATAL: another remote_deploy.sh is already running"; exit 1; }
fi

set -a; source "$ENV_FILE"; set +a

# A301: fail-fast whitelist must cover EVERY secret this script passes with an
# empty default — an empty CDS_COLUMN_ENCRYPTION_KEY / MFA key used to boot the
# lane with silent fallback keys (the compose side closed the same hole with
# ${VAR:?} in A283).
for v in POSTGRES_PASSWORD REDIS_PASSWORD CLICKHOUSE_PASSWORD MINIO_ROOT_PASSWORD VAULT_TOKEN \
         CDS_JWT_SECRET_KEY CDS_KMS_SERVICE_TOKEN CDS_CUBE_AUDIT_SERVICE_TOKEN CDS_CUBE_TEMPLATE_SIGNING_KEY \
         CDS_COLUMN_ENCRYPTION_KEY CDS_MFA_SECRET_ENCRYPTION_KEY; do
  if [ -z "${!v:-}" ]; then echo "FATAL: $v is empty in $ENV_FILE"; exit 1; fi
done
# The Postgres password travels inside CDS_DATABASE_URL: URL-reserved chars
# would silently retarget the DSN (host/fragment parsing) into "cannot connect
# to cds-postgres" style ghosts. make_env.sh generates hex — hold that contract.
case "$POSTGRES_PASSWORD" in
  *[' @:/#?%']*) echo "FATAL: POSTGRES_PASSWORD contains URL-reserved chars — CDS_DATABASE_URL would misparse"; exit 1;;
esac
# cert-without-key is a runtime puzzle, not a posture choice: fail at deploy.
if [ -n "${CDS_FEDERATION_CERT:-}" ] && [ -z "${CDS_FEDERATION_KEY:-}" ]; then
  echo "FATAL: CDS_FEDERATION_CERT set without CDS_FEDERATION_KEY (pair them or drop both)"; exit 1
fi
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
  # briefly hold the published port inside its netns — wait for full teardown
  # and FAIL (not proceed) when it never lands: proceeding here produced
  # "name already in use" / "port is already allocated" deaths mid-stack.
  local _i _st=gone
  for _i in 1 2 3 4 5 6 7 8 9 10; do
    docker ps -q -f name="^${name}$" | grep -q . || { _st=gone; break; }
    sleep 1
    _st=present
  done
  [ "$_st" = gone ] || { echo "FATAL: $name still present after 10s teardown wait (crash-loop restart?); aborting before docker run"; exit 1; }
  docker run -d --name "$name" --network "$NET" --restart unless-stopped "${args[@]}" "$image" "${cmd[@]+"${cmd[@]}"}"
}

# Array-valued health probe: wait_healthy <name> <tries> <cmd...>.
# (The old string form word-split AND glob-expanded the command — a password
# containing * or ? was expanded against the host CWD before ever reaching the
# container.) Also fails fast when the container is crash-looping instead of
# burning tries×2s on a corpse.
wait_healthy() {
  local name="$1" tries="$2" i st; shift 2
  for ((i=1; i<=tries; i++)); do
    st=$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo missing)
    case "$st" in
      exited|restarting|dead|missing)
        echo "FATAL: $name is $st while waiting for health (last logs):"
        docker logs --tail 20 "$name" 2>&1 || true
        exit 1;;
    esac
    docker exec "$name" "$@" >/dev/null 2>&1 && { echo "healthy: $name"; return 0; }
    sleep 2
  done
  echo "FATAL: $name not healthy after $((tries*2))s: $*"
  docker logs --tail 20 "$name" 2>&1 || true
  exit 1
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
wait_healthy cds-postgres 45 pg_isready -U cds
# strict check: redis-cli exits 0 even on WRONGPASS, so grep the PONG.
wait_healthy cds-redis 45 sh -c 'redis-cli -a "$1" --no-auth-warning ping | grep -q PONG' _ "$REDIS_PASSWORD"
wait_healthy cds-minio 45 mc ready local
wait_healthy_port 8181 30
# a vault crash-loop from an earlier failed deploy can leave its restart cycle
# holding port 8200 briefly; allow the restart backoff to settle before FATAL.
wait_healthy cds-vault 60 vault status -address=http://localhost:8200
# D-e2e-11: the app KMS uses Vault TRANSIT for KEK management (hsm_adapter
# VaultTransitAdapter). The dev-mode Vault ships without the transit engine,
# so every KEK op 500s with "HSM unavailable — cannot generate KEK". Enable
# it on every deploy (dev Vault is in-memory and resets on container restart).
VROOT="$(docker exec cds-vault printenv VAULT_DEV_ROOT_TOKEN_ID)"
transit_out=$(docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN="$VROOT" \
  cds-vault vault secrets enable -path=transit transit 2>&1) \
  || { printf '%s\n' "$transit_out" | grep -q 'already in use' \
       || { echo "FATAL: vault transit enable failed: $transit_out"; exit 1; }; }
# verify the mount instead of substring-grepping `secrets list` output
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN="$VROOT" \
  cds-vault vault secrets list -format=table \
  | awk '{print $1}' | grep -qx 'transit/' \
  || { echo "FATAL: transit engine not mounted (verified via secrets list)"; exit 1; }

echo "== migrate schema (prod posture skips create_all — main.py:80) =="
docker run --rm --network "$NET" \
  -e CDS_DATABASE_URL="postgresql+asyncpg://cds:${POSTGRES_PASSWORD}@cds-postgres:5432/cds" \
  -e CDS_DEBUG=false \
  --entrypoint bash cds-api:latest -c "python -m alembic upgrade head"

echo "== api =="
# A301 (batch-87): the lane runs the PRODUCTION startup posture — CDS_DEBUG
# defaults false so validate_security_config's fifteen boot gates actually run
# here (the old `CDS_DEBUG:-true` default silently bypassed all of them, which
# is how the lane could boot with an empty column DEK). The three fallback
# switches are pinned false exactly as docker-compose.prod.yml does: Vault is
# up with transit (no software-HSM fallback), the lane offers no bwrap/firecracker
# sandbox (legacy off → SS-13 honest 400), and gmssl ships in the image.
run cds-api cds-api:latest \
  -p 8000:8000 \
  -e CDS_DEBUG="${CDS_DEBUG:-false}" \
  -e CDS_SECCOMP_FALLBACK_ALLOWED=false \
  -e CDS_HSM_SOFTWARE_FALLBACK_ALLOWED=false \
  -e CDS_FEDERATION_ENABLED="${CDS_FEDERATION_ENABLED:-false}" \
  -e CDS_COLUMN_ENCRYPTION_KEY="$CDS_COLUMN_ENCRYPTION_KEY" \
  -e CDS_SM3_FALLBACK_ALLOWED="${CDS_SM3_FALLBACK_ALLOWED:-false}" \
  -e CDS_FEDERATION_JWT_KEY_REQUIRED="${CDS_FEDERATION_JWT_KEY_REQUIRED:-true}" \
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
  -e CDS_FEDERATION_KEY="${CDS_FEDERATION_KEY:-}" \
  -e CDS_ATTESTATION_ALLOWED_MEASUREMENTS="" \
  -e CDS_SESSION_CLEANUP_INTERVAL_SECONDS="300" \
  -e CDS_KMS_REQUIRE_ATTESTATION="${CDS_KMS_REQUIRE_ATTESTATION:-false}" \
  -e CDS_KMS_SERVICE_TOKEN="$CDS_KMS_SERVICE_TOKEN" \
  -e CDS_DEV_SANDBOX_REQUIRE_CONTRACT="false" \
  -e CDS_SESSION_DEFAULT_IDLE_POLICY="kill" \
  -e CDS_EXEC_STREAM_MAX_CONCURRENT="3" \
  -e CDS_AUTH_MFA_ENABLED="${CDS_AUTH_MFA_ENABLED:-true}" \
  -e CDS_MFA_SECRET_ENCRYPTION_KEY="$CDS_MFA_SECRET_ENCRYPTION_KEY" \
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
# transient /health flake must not swallow DEPLOY_DONE (callers poll for the
# sentinel and would rerun a healthy-but-"failed" deploy).
curl -fsS http://localhost:8000/health || echo "WARN: final /health probe failed"
echo
echo DEPLOY_DONE
